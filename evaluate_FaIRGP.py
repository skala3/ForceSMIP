"""
Description : Fits FaIR-contrained GP for global temperature response emulation

Usage: evaluate_FaIRGP.py  [options] --cfg=<path_to_config> --o=<output_dir>

Options:
  --cfg=<path_to_config>           Path to YAML configuration file to use.
  --o=<output_dir>                 Output directory.
  --device=<device_index>          Device to use [default: cpu]
  --plot                           Outputs plots.
"""
import os
import yaml
import logging
import copy
from docopt import docopt
import tqdm
import pandas as pd
import torch
from gpytorch import distributions
from sklearn.model_selection import KFold
from src.evaluation import compute_scores, dump_plots
from fit_FaIRGP import make_data, migrate_to_device, make_model, fit


def main(args, cfg):
    # Make cross-validation folds iterator
    folds = make_folds_iterator(cfg)
    folds = tqdm.tqdm(folds, desc='Folds', total=len(cfg['dataset']['keys']) - 1)

    # Initialize empty list for scores
    scores = []

    # Iterate over folds
    for i, (train_keys, test_keys) in enumerate(folds):
        train_data, test_data = make_train_test_data(cfg=cfg,
                                                     train_keys=train_keys,
                                                     test_keys=test_keys)

        # Move needed tensors only to device
        train_data = migrate_to_device(data=train_data, device=device)
        test_data = migrate_to_device(data=test_data, device=device)

        # Instantiate model
        model = make_model(cfg=cfg, data=train_data).to(device)
        logging.info(f"{model}")

        # Fit model
        logging.info("\n Fitting model")
        model = fit(model=model, data=train_data, cfg=cfg)

        # Predict on left out scenario
        posterior_dist = predict(model=model, test_data=test_data)

        # Evaluate on left out scenario
        fold_output_dir = os.path.join(args['--o'], f"fold_{test_data.scenarios.names[0]}")
        fold_scores = evaluate(posterior_dist=posterior_dist,
                               test_data=test_data,
                               model=model,
                               plot=args['--plot'],
                               output_dir=fold_output_dir,
                               cfg=cfg)
        scores.append(fold_scores)

        # Dump scores
        dump_scores(scores=scores, output_dir=args['--o'])


def make_folds_iterator(cfg):
    keys = [key for key in cfg['dataset']['keys'] if key != 'historical']
    folds = KFold(n_splits=len(keys))
    for train_idx, test_idx in folds.split(keys):
        train_keys = ['historical'] + [keys[idx] for idx in train_idx]
        test_keys = [keys[idx] for idx in test_idx]
        yield train_keys, test_keys


def make_train_test_data(cfg, train_keys, test_keys):
    # Duplicate configuration file
    train_cfg = copy.deepcopy(cfg)
    test_cfg = copy.deepcopy(cfg)

    # Update keys with ones from fold
    train_cfg['dataset']['keys'] = train_keys
    test_cfg['dataset']['keys'] = test_keys

    # Make corresponding datsets
    train_data = make_data(cfg=train_cfg)
    test_data = make_data(cfg=test_cfg)
    return train_data, test_data


def predict(model, test_data):
    model.eval()
    with torch.no_grad():
        test_posterior = model(test_data.scenarios)
        noisy_test_posterior = model.likelihood(test_posterior)
    test_tas_fair = model._compute_mean(test_data.scenarios)
    noisy_test_posterior = distributions.MultivariateNormal(mean=noisy_test_posterior.mean + test_tas_fair,
                                                            covariance_matrix=noisy_test_posterior.lazy_covariance_matrix)
    return noisy_test_posterior


def evaluate(posterior_dist, test_data, model, plot, output_dir, cfg):
    # Create output directory if doesn't exists
    os.makedirs(output_dir, exist_ok=True)

    # Dump plots
    if plot:
        dump_plots(posterior_dist=posterior_dist,
                   test_scenarios=test_data.scenarios,
                   model=model,
                   output_dir=output_dir)

    # Compute scores
    scores = compute_scores(posterior_dist, test_data.scenarios)
    return scores


def dump_scores(scores, output_dir):
    scores_df = pd.DataFrame(data=scores)
    scores_df.to_csv(os.path.join(output_dir, 'cv-scores.csv'), index=False)


if __name__ == "__main__":
    # Read input args
    args = docopt(__doc__)

    # Load config file
    with open(args['--cfg'], "r") as f:
        cfg = yaml.safe_load(f)

    # Setup logging
    logging.basicConfig(level=logging.INFO)
    logging.info(f'Arguments: {args}\n')
    logging.info(f'Configuration file: {cfg}\n')

    # Create output directory if doesn't exists
    os.makedirs(args['--o'], exist_ok=True)
    with open(os.path.join(args['--o'], 'cfg.yaml'), 'w') as f:
        yaml.dump(cfg, f)

    # Setup global variable for device
    if torch.cuda.is_available() and args['--device'].isdigit():
        device = torch.device(f"cuda:{args['--device']}")
    else:
        device = torch.device('cpu')

    # Run session
    main(args, cfg)
