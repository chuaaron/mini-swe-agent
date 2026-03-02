from minisweagent.run_locbench import _build_overrides, _parse_args


def test_feedback_cli_overrides_are_collected():
    args = _parse_args(
        [
            "--mode",
            "bash",
            "--feedback-loop",
            "--feedback-mode",
            "hybrid",
            "--feedback-every-n-steps",
            "2",
            "--feedback-max-rounds",
            "7",
            "--feedback-submission-gate",
        ]
    )
    overrides = _build_overrides(args)

    run_overrides = overrides["run"]
    assert run_overrides["mode"] == "bash"
    assert run_overrides["feedback_loop"] is True
    assert run_overrides["feedback_mode"] == "hybrid"
    assert run_overrides["feedback_every_n_steps"] == 2
    assert run_overrides["feedback_max_rounds"] == 7
    assert run_overrides["feedback_submission_gate"] is True


def test_feedback_cli_no_flags_disable_features():
    args = _parse_args(
        [
            "--mode",
            "bash",
            "--no-feedback-loop",
            "--no-feedback-submission-gate",
        ]
    )
    overrides = _build_overrides(args)
    run_overrides = overrides["run"]

    assert run_overrides["feedback_loop"] is False
    assert run_overrides["feedback_submission_gate"] is False
