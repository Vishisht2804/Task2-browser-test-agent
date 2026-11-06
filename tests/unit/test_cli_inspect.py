from browser_agent.cli import build_parser, main


def test_inspect_subcommand_parses_url_and_options() -> None:
    args = build_parser().parse_args(
        [
            "inspect",
            "--url",
            "https://example.com",
            "--headed",
            "--json",
            "--max-elements-per-frame",
            "250",
        ]
    )

    assert args.command == "inspect"
    assert args.url == "https://example.com"
    assert args.headed is True
    assert args.json is True
    assert args.max_elements_per_frame == 250


def test_inspect_rejects_zero_element_limit(capsys) -> None:
    result = main(
        [
            "inspect",
            "--url",
            "https://example.com",
            "--max-elements-per-frame",
            "0",
        ]
    )

    assert result == 2
    assert "at least 1" in capsys.readouterr().err
