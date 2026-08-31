import importlib.util
from pathlib import Path


EXAMPLE = Path(__file__).parents[1] / "examples" / "crash_star_auto_play.py"
SPEC = importlib.util.spec_from_file_location("crash_star_auto_play", EXAMPLE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_countdown_requires_betting_panel_and_valid_range():
    assert MODULE.countdown_from_items([("starts in 14 second", 0.55, 0.66)]) == 14
    assert MODULE.countdown_from_items([("starts in 15 second", 0.55, 0.66)]) is None
    assert MODULE.countdown_from_items([("starts in 8 second", 0.10, 0.10)]) is None


def test_flight_multiplier_ignores_history_strip():
    assert MODULE.flight_multiplier_from_items([("4.86x", 0.82, 0.54)]) is None
    assert MODULE.flight_multiplier_from_items([("2.05x", 0.55, 0.70)]) == 2.05


def test_state_and_coordinate_scaling():
    assert MODULE.state_from_items([("TAKE", 0.5, 0.9), ("1.44x", 0.5, 0.7)]) == ("flying", 1.44)
    assert MODULE.scaled((155, 950), (720, 1600)) == (232, 1423)


def test_random_bets_stay_inside_calibrated_betting_area():
    class FixedRandom:
        def randint(self, left, right):
            return left

    points = MODULE.random_bet_points(3, (720, 1600), FixedRandom())
    assert points == [(278, 1049), (278, 1049), (278, 1049)]


def test_parser_defaults_to_safe_low_chip_and_no_cash_out():
    args = MODULE.build_parser().parse_args([])
    assert args.chip == 100
    assert args.cash_out_at == 0
    assert (args.min_bets, args.max_bets) == (2, 10)
