"""Locate packaged legacy runtime inputs independently of the working directory."""
from pathlib import Path


def legacy_root():
    checkout = Path(__file__).resolve().parent.parent
    if (checkout/'scripts/guided_demo.py').is_file() and (checkout/'Cargo.toml').is_file():
        return checkout
    packaged = Path(__file__).resolve().parent/'_legacy'
    if not (packaged/'scripts/guided_demo.py').is_file():
        raise RuntimeError('The installed package is incomplete. Reinstall invariant-workspace from a verified wheel.')
    return packaged


def source_checkout():
    checkout = Path(__file__).resolve().parent.parent
    if not (checkout/'scripts/test_mvp_acceptance.py').is_file():
        raise RuntimeError('Legacy acceptance is a maintainer command requiring a source checkout. '
                           'For experiment acceptance, use invariant experiment acceptance --checkout PATH.')
    return checkout
