import hashlib
import hmac
from dataclasses import dataclass

SUITS = ("♠", "♥", "♦", "♣")
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")


def server_seed_commitment(server_seed: str) -> str:
    return hashlib.sha256(server_seed.encode("utf-8")).hexdigest()


@dataclass
class FairRandom:
    server_seed: str
    client_seed: str
    nonce: int = 0
    counter: int = 0

    def randbelow(self, n: int) -> int:
        """Deterministic HMAC-SHA256 stream with rejection sampling."""
        if n <= 0:
            raise ValueError("n must be positive")
        ceiling = 1 << 256
        limit = ceiling - (ceiling % n)
        while True:
            message = f"{self.client_seed}:{self.nonce}:{self.counter}".encode()
            digest = hmac.new(
                bytes.fromhex(self.server_seed), message, hashlib.sha256
            ).digest()
            self.counter += 1
            candidate = int.from_bytes(digest, "big")
            if candidate < limit:
                return candidate % n


def build_deck() -> list[dict[str, str]]:
    return [{"rank": rank, "suit": suit} for suit in SUITS for rank in RANKS]


def shuffled_deck(
    server_seed: str, client_seed: str, nonce: int = 0
) -> list[dict[str, str]]:
    deck = build_deck()
    rng = FairRandom(server_seed, client_seed, nonce)
    for index in range(len(deck) - 1, 0, -1):
        other = rng.randbelow(index + 1)
        deck[index], deck[other] = deck[other], deck[index]
    return deck


def hand_value(hand: list[dict[str, str]]) -> int:
    total = 0
    aces = 0
    for card in hand:
        rank = card["rank"]
        if rank == "A":
            aces += 1
            total += 11
        elif rank in ("J", "Q", "K"):
            total += 10
        else:
            total += int(rank)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def is_natural(hand: list[dict[str, str]]) -> bool:
    return len(hand) == 2 and hand_value(hand) == 21


def resolve_blackjack(
    player: list[dict[str, str]], dealer: list[dict[str, str]]
) -> str:
    player_total, dealer_total = hand_value(player), hand_value(dealer)
    player_natural, dealer_natural = is_natural(player), is_natural(dealer)
    if player_natural or dealer_natural:
        if player_natural and dealer_natural:
            return "push"
        return "player" if player_natural else "dealer"
    if player_total > 21:
        return "dealer"
    if dealer_total > 21:
        return "player"
    if player_total > dealer_total:
        return "player"
    if player_total < dealer_total:
        return "dealer"
    return "push"


def blackjack_payout(bet: int, outcome: str, natural_win: bool = False) -> int:
    """Return gross payout, including returned stake (a natural pays 3:2 net)."""
    if bet < 0:
        raise ValueError("bet must be nonnegative")
    if outcome == "push":
        return bet
    if outcome == "player":
        return bet + (bet * 3 // 2) if natural_win else bet * 2
    if outcome == "dealer":
        return 0
    raise ValueError(f"Unknown blackjack outcome: {outcome}")


def dice_roll(server_seed: str, client_seed: str, nonce: int = 0) -> int:
    return FairRandom(server_seed, client_seed, nonce).randbelow(6) + 1


def weighted_choice(
    options: list[tuple[int, int]], server_seed: str, client_seed: str, nonce: int = 0
) -> int:
    total_weight = sum(weight for _, weight in options)
    ticket = FairRandom(server_seed, client_seed, nonce).randbelow(total_weight)
    for value, weight in options:
        if ticket < weight:
            return value
        ticket -= weight
    raise AssertionError("unreachable")
