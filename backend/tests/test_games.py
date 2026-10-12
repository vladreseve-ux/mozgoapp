import hashlib

from app.games import (
    blackjack_payout,
    dice_roll,
    hand_value,
    is_natural,
    resolve_blackjack,
    server_seed_commitment,
    shuffled_deck,
    weighted_choice,
)


def card(rank: str, suit: str = "♠") -> dict[str, str]:
    return {"rank": rank, "suit": suit}


def test_aces_count_as_eleven_or_one_as_needed():
    assert hand_value([card("A"), card("9")]) == 20
    assert hand_value([card("A"), card("9"), card("5")]) == 15
    assert hand_value([card("A"), card("A"), card("9")]) == 21


def test_natural_and_blackjack_outcomes():
    natural = [card("A"), card("K")]
    assert is_natural(natural)
    assert resolve_blackjack(natural, [card("10"), card("9")]) == "player"
    assert resolve_blackjack(natural, [card("A"), card("Q")]) == "push"
    assert resolve_blackjack([card("10"), card("8")], [card("10"), card("8")]) == "push"
    assert (
        resolve_blackjack([card("10"), card("9")], [card("10"), card("7"), card("5")])
        == "player"
    )


def test_blackjack_gross_payouts():
    assert blackjack_payout(100, "player") == 200
    assert blackjack_payout(100, "player", natural_win=True) == 250
    assert blackjack_payout(100, "push") == 100
    assert blackjack_payout(100, "dealer") == 0


def test_fair_shuffle_is_repeatable_and_contains_52_unique_cards():
    first = shuffled_deck("01" * 32, "client-a")
    second = shuffled_deck("01" * 32, "client-a")
    changed = shuffled_deck("01" * 32, "client-b")
    assert first == second
    assert first != changed
    assert len(first) == len({(item["rank"], item["suit"]) for item in first}) == 52


def test_dice_and_weighted_prize_repeat_for_same_seeds():
    assert dice_roll("ab" * 32, "client", 7) == dice_roll("ab" * 32, "client", 7)
    prize = weighted_choice([(5, 30), (10, 70)], "cd" * 32, "player")
    assert prize in (5, 10)


def test_server_seed_reveal_matches_commitment():
    server_seed = "ab" * 32
    assert (
        server_seed_commitment(server_seed)
        == hashlib.sha256(server_seed.encode()).hexdigest()
    )
    assert len(server_seed_commitment(server_seed)) == 64
