from glob import glob
import os

# import appdirs
import pytest

from .fixtures import BIDS_TESTDATA_SELECTION


@pytest.mark.parametrize("dataset", BIDS_TESTDATA_SELECTION)
def test_validate_bids(bids_examples, tmp_path, dataset):
    from ..validate import validate_bids

    selected_dataset = os.path.join(bids_examples, dataset)
    validation_result = validate_bids(selected_dataset, report=True)
    for i in validation_result:
        assert not hasattr(i, "severtiy")


def test_report_path(bids_examples, tmp_path):
    from ..validate import validate_bids

    # pid = os.getpid()
    # log_dir = appdirs.user_log_dir("dandi-cli", "dandi")
    # report_expression = os.path.join(log_dir, f"bids-validator-report_*-{pid}.log")
    # assert len(glob(report_expression)) == 1

    report_path = os.path.join(tmp_path, "inplace_bids-validator-report.log")
    selected_dataset = os.path.join(bids_examples, BIDS_TESTDATA_SELECTION[0])
    _ = validate_bids(
        selected_dataset,
        report_path=report_path,
    )

    # Check if a report is being produced.
    assert len(glob(report_path)) == 1


@pytest.mark.parametrize(
    "dataset", ["invalid_asl003", "invalid_eeg_cbm", "invalid_pet001"]
)
def test_validate_bids_errors(bids_error_examples, dataset):
    # This only checks that the error we found is correct, not that we found all errors.
    # ideally make a list and erode etc.
    import json
    import pathlib

    from ..validate import validate_bids

    selected_dataset = os.path.join(bids_error_examples, dataset)
    validation_result = validate_bids(selected_dataset, report=True)
    with open(os.path.join(selected_dataset, ".ERRORS.json")) as f:
        expected_errors = json.load(f)
    for i in validation_result:
        if i.id == "BIDS.MATCH":
            continue
        error_id = i.id
        if i.path:
            error_path = i.path
            relative_error_path = os.path.relpath(error_path, i.dataset_path)
            relative_error_path = pathlib.Path(relative_error_path).as_posix()
            assert (
                relative_error_path
                in expected_errors[error_id.lstrip("BIDS.")]["scope"]
            )
        else:
            assert i.id.lstrip("BIDS.") in expected_errors.keys()
