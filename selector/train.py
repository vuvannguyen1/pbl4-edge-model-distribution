"""Backward-compatible entry point for the adaptive selector trainer."""
from training.train_selector import parse_args, train


if __name__ == "__main__":
    train(parse_args())
