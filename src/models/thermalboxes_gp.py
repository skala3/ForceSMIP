import torch
import gpytorch
from gpytorch import settings, distributions
from gpytorch.models import GP
from .exact_prediction_strategy import prediction_strategy
from .utils import compute_means, compute_I, compute_covariance, compute_mF, compute_kFT


class ThermalBoxesGP(GP):
    def __init__(self, scenario_dataset, kernel, q, d, likelihood):
        super().__init__()
        # Register input data
        self.train_scenarios = scenario_dataset

        # Setup mean, kernel and likelihood
        self.register_buffer('train_mean', self._compute_mean(self.train_scenarios))
        self.kernel = kernel
        self.likelihood = likelihood

        # Create training targets
        train_targets = self.train_scenarios.response_var - self.train_mean
        self.register_buffer('train_targets', train_targets)

        # Register thermal boxes parameters
        self.d = torch.from_numpy(d).float()
        self.q = torch.from_numpy(q).float()
        self.nboxes = len(d)

        # Initialize prediction strategy
        self.prediction_strategy = None

    def _clear_cache(self):
        self.prediction_strategy = None

    def _setup_prediction_strategy(self):
        train_prior_dist = self.train_prior_dist()

        # Create the prediction strategy for
        self.prediction_strategy = prediction_strategy(train_prior_dist=train_prior_dist,
                                                       train_targets=self.train_targets,
                                                       likelihood=self.likelihood)

    def _compute_mean(self, scenario_dataset):
        means = compute_means(scenario_dataset)
        means = torch.cat([v for v in means.values()]).sum(dim=-1)
        return means

    def _compute_covariance(self, scenario_dataset):
        I = compute_I(scenario_dataset, self.kernel, self.q, self.d)
        covar = compute_covariance(scenario_dataset, I, self.q, self.d)
        covar = gpytorch.add_jitter(covar)
        return covar

    def train_prior_dist(self):
        train_mean = torch.zeros_like(self.train_scenarios.response_var)
        train_covar = self._compute_covariance(self.train_scenarios)
        train_prior_dist = distributions.MultivariateNormal(train_mean, train_covar)
        return train_prior_dist

    def forward(self, scenario_dataset):
        mean = torch.zeros_like(scenario_dataset.response_var)
        covar = self._compute_covariance(scenario_dataset)
        prior_dist = distributions.MultivariateNormal(mean, covar)
        return prior_dist

    def _compute_forcing_mean(self, scenario_dataset):
        mFs = compute_mF(scenario_dataset)
        mF = torch.cat([v for v in mFs.values()])
        return mF

    def forcing_posterior(self, test_scenarios):
        mF = self._compute_forcing_mean(test_scenarios)

        mu, sigma = self.train_scenarios.mu_inputs, self.train_scenarios.sigma_inputs
        test_scenario_emissions_std = (test_scenarios.inputs - mu) / sigma
        kFF = self.kernel(test_scenario_emissions_std).evaluate()

        kFT = compute_kFT(test_scenarios, self.train_scenarios, self.kernel, self.q, self.d)

        covar = self._compute_covariance(self.train_scenarios)
        covar = covar + self.likelihood.compute_covariance(covar.size(0))
        chol = torch.linalg.cholesky(gpytorch.add_jitter(covar))
        kFT_covarinv = torch.cholesky_solve(kFT.T, chol).T

        posterior_mF = mF + kFT_covarinv @ self.train_targets
        posterior_kFF = kFF - kFT_covarinv @ kFT.T
        posterior_kFF = gpytorch.add_jitter(posterior_kFF)

        while True:
            try:
                posterior_F = distributions.MultivariateNormal(posterior_mF, posterior_kFF)
                break
            except torch._C._LinAlgError:
                posterior_kFF = gpytorch.add_jitter(posterior_kFF)
        return posterior_F

    def __call__(self, *args, **kwargs):
        # Training mode: optimizing
        if self.training:
            res = self.train_prior_dist()
            return res

        # Prior mode
        elif settings.prior_mode.on():
            res = super().__call__(*args, **kwargs)
            return res

        # Posterior mode
        else:
            if self.prediction_strategy is None:
                self._setup_prediction_strategy()

            # Concatenate the input to the training input
            test_scenarios = args[0]
            train_test_scenarios = self.train_scenarios + test_scenarios

            # Get the joint distribution for training/test data
            full_prior_dist = super().__call__(train_test_scenarios, **kwargs)
            full_mean, full_covar = full_prior_dist.loc, full_prior_dist.lazy_covariance_matrix

            # Determine the shape of the joint distribution
            batch_shape = full_prior_dist.batch_shape
            joint_shape = full_prior_dist.event_shape
            test_shape = torch.Size([joint_shape[0] - self.prediction_strategy.train_shape[0]])

            # Make the prediction
            predictive_mean, predictive_covar = self.prediction_strategy.exact_prediction(full_mean, full_covar)

            # Reshape predictive mean to match the appropriate event shape
            predictive_mean = predictive_mean.view(*batch_shape, *test_shape).contiguous()
            return full_prior_dist.__class__(predictive_mean, predictive_covar)
