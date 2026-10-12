import { useEffect, useMemo, useState } from "react";
import {
  blackjackAction,
  createFairCommit,
  createInvoice,
  getPlayer,
  getTrophyStatus,
  generateTrophy,
  generateAvatar,
  playDice,
  playWheel,
  startBlackjack,
  type ApiGameResult,
  type BlackjackState,
  type Card,
  type FairCommit,
  type Player,
  type TrophyAsset,
} from "./api";
import { openTelegramInvoice } from "./telegram";

type Mode = "blackjack" | "dice" | "wheel";
type DiceMode = "exact" | "odd" | "even" | "low" | "high";

const format = (value: number) => new Intl.NumberFormat("ru-RU").format(value);
const suits = ["♠", "♥", "♦", "♣"];
const ranks = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"];

function randomCard(): Card {
  return { rank: ranks[Math.floor(Math.random() * ranks.length)], suit: suits[Math.floor(Math.random() * 4)] };
}

function cardTotal(cards: Card[]): number {
  let total = 0;
  let aces = 0;
  cards.forEach((card) => {
    if (card.rank === "A") {
      total += 11;
      aces += 1;
    } else if (["J", "Q", "K"].includes(card.rank ?? "")) total += 10;
    else total += Number(card.rank ?? 0);
  });
  while (total > 21 && aces) {
    total -= 10;
    aces -= 1;
  }
  return total;
}

function App({ initData, demoMode }: { initData: string; demoMode: boolean }) {
  const [player, setPlayer] = useState<Player | null>(null);
  const [demoBalance, setDemoBalance] = useState(4200);
  const [mode, setMode] = useState<Mode>("blackjack");
  const [game, setGame] = useState<BlackjackState | null>(null);
  const [lastResult, setLastResult] = useState<ApiGameResult | null>(null);
  const [balance, setBalance] = useState(4200);
  const [bet, setBet] = useState(100);
  const [diceMode, setDiceMode] = useState<DiceMode>("exact");
  const [dicePick, setDicePick] = useState(6);
  const [commit, setCommit] = useState<FairCommit | null>(null);
  const [busy, setBusy] = useState(false);
  const [shopOpen, setShopOpen] = useState(false);
  const [trophy, setTrophy] = useState<TrophyAsset | null>(null);
  const [avatarAsset, setAvatarAsset] = useState<TrophyAsset | null>(null);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (demoMode) return;
    getPlayer(initData)
      .then((data) => {
        setPlayer(data);
        setBalance(data.balance);
      })
      .catch((error: Error) => setNotice(error.message));
  }, [demoMode, initData]);

  const displayName = player?.first_name || window.Telegram?.WebApp?.initDataUnsafe?.user?.first_name || "Игрок";
  const visibleBalance = demoMode ? demoBalance : balance;
  const fairnessHash = commit?.server_seed_hash ?? game?.server_seed_hash ?? lastResult?.server_seed_hash;
  const fairnessClientSeed = commit?.client_seed ?? game?.result?.client_seed ?? lastResult?.result?.client_seed;
  const revealedSeed = game?.result?.server_seed ?? lastResult?.result?.server_seed;
  const modeLabel = useMemo(
    () => ({ blackjack: "Блэкджек", dice: "Кубик", wheel: "Фортуна" })[mode],
    [mode],
  );

  function selectMode(nextMode: Mode) {
    setMode(nextMode);
    setCommit(null);
    setLastResult(null);
    setTrophy(null);
    setNotice("");
  }

  async function handleTrophy() {
    if (demoMode) {
      setNotice("Рендер трофеев включается на сервере с Replicate API.");
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      let asset: TrophyAsset;
      if (avatarAsset && !["succeeded", "successful", "failed", "canceled"].includes(avatarAsset.status)) {
        asset = await getTrophyStatus(initData, avatarAsset.prediction_id);
        setAvatarAsset(asset);
        const updated = await getPlayer(initData);
        setPlayer(updated);
        setBalance(updated.balance);
      } else if ((player?.avatar_credits ?? 0) > 0) {
        asset = await generateAvatar(initData);
        setAvatarAsset(asset);
        const updated = await getPlayer(initData);
        setPlayer(updated);
        setBalance(updated.balance);
      } else {
        if (mode !== "blackjack" || !game || game.status !== "finished" || !game.result) {
          setNotice("Нужен выигрыш в блэкджек или купленный AI-аватар.");
          setBusy(false);
          return;
        }
        asset = trophy
          ? await getTrophyStatus(initData, trophy.prediction_id)
          : await generateTrophy(initData, game.game_id);
        setTrophy(asset);
      }
      if (asset.status === "failed") setNotice("Не удалось создать трофей. Попробуйте позже.");
      else if (!asset.image_url) setNotice(`AI Canvas: генерация ${asset.status}. Нажмите карточку ещё раз для проверки.`);
    } catch (error) {
      setNotice((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function prepareCommit(): Promise<FairCommit | null> {
    if (demoMode) {
      const demoCommit = {
        commit_id: "demo",
        client_seed: crypto.randomUUID(),
        server_seed_hash: "8af13d9c2a6e71b4f0902cc",
      };
      setCommit(demoCommit);
      setNotice("Демо-сид создан. Нажмите ещё раз, чтобы начать раунд.");
      return null;
    }
    try {
      const next = await createFairCommit(initData);
      setCommit(next);
      setNotice("Хеш сида зафиксирован. Подтвердите ставку, чтобы начать раунд.");
      return null;
    } catch (error) {
      setNotice((error as Error).message);
      return null;
    }
  }

  async function dealBlackjack() {
    if (busy) return;
    setNotice("");
    if (!commit) {
      await prepareCommit();
      return;
    }
    setBusy(true);
    try {
      if (demoMode) {
        const initial: BlackjackState = {
          game_id: "demo-round",
          game: "blackjack",
          status: "active",
          bet,
          hand: [randomCard(), randomCard()],
          dealer: [randomCard(), { hidden: true }],
          hand_value: 0,
          dealer_value: null,
          can_double: true,
          server_seed_hash: commit.server_seed_hash,
          balance: demoBalance - bet,
        };
        initial.hand_value = cardTotal(initial.hand);
        setDemoBalance((current) => current - bet);
        setGame(initial);
      } else {
        const state = await startBlackjack(initData, bet, commit);
        setGame(state);
        setBalance(state.balance ?? balance - bet);
      }
      setCommit(null);
      setLastResult(null);
      setTrophy(null);
    } catch (error) {
      setNotice((error as Error).message);
      setCommit(null);
    } finally {
      setBusy(false);
    }
  }

  async function doBlackjackAction(action: "hit" | "stand" | "double") {
    if (!game || busy) return;
    setBusy(true);
    setNotice("");
    try {
      if (demoMode) {
        const playerHand = [...game.hand];
        const dealerHand = game.dealer.filter((card) => !card.hidden);
        let nextBet = game.bet;
        if (action === "double") {
          nextBet *= 2;
          setDemoBalance((current) => current - game.bet);
          playerHand.push(randomCard());
        } else if (action === "hit") playerHand.push(randomCard());

        const shouldFinish = action === "stand" || action === "double" || cardTotal(playerHand) >= 21;
        if (!shouldFinish) {
          setGame({ ...game, hand: playerHand, hand_value: cardTotal(playerHand), can_double: false });
        } else {
          while (cardTotal(dealerHand) < 17) dealerHand.push(randomCard());
          const p = cardTotal(playerHand);
          const d = cardTotal(dealerHand);
          const outcome = p > 21 ? "dealer" : d > 21 || p > d ? "player" : p === d ? "push" : "dealer";
          const natural = playerHand.length === 2 && p === 21;
          const payout = outcome === "push" ? nextBet : outcome === "player" ? nextBet * (natural ? 2.5 : 2) : 0;
          setDemoBalance((current) => current + payout);
          setGame({
            ...game,
            bet: nextBet,
            hand: playerHand,
            dealer: dealerHand,
            hand_value: p,
            dealer_value: d,
            status: "finished",
            can_double: false,
            result: {
              outcome,
              payout,
              net: payout - nextBet,
              server_seed: "demo-seed",
              client_seed: "demo-client-seed",
              nonce: 0,
            },
          });
        }
      } else {
        const state = await blackjackAction(initData, game.game_id, action);
        setGame(state);
        if (state.balance !== undefined) setBalance(state.balance);
      }
    } catch (error) {
      setNotice((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function rollDice() {
    if (busy) return;
    if (!commit) {
      await prepareCommit();
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      if (demoMode) {
        const roll = Math.floor(Math.random() * 6) + 1;
        const won =
          diceMode === "exact"
            ? roll === dicePick
            : diceMode === "odd"
              ? roll % 2 === 1
              : diceMode === "even"
                ? roll % 2 === 0
                : diceMode === "low"
                  ? roll <= 3
                  : roll >= 4;
        const payout = won ? Math.floor(bet * (diceMode === "exact" ? 5.7 : 1.9)) : 0;
        setDemoBalance((current) => current - bet + payout);
        setLastResult({
          game_id: "demo-dice",
          game: "dice",
          balance: demoBalance - bet + payout,
          server_seed_hash: commit.server_seed_hash,
          result: { roll, won, payout, net: payout - bet, server_seed: "demo-seed" },
        });
      } else {
        const result = await playDice(
          initData,
          bet,
          diceMode,
          diceMode === "exact" ? dicePick : undefined,
          commit,
        );
        setLastResult(result);
        setBalance(result.balance);
      }
      setCommit(null);
    } catch (error) {
      setNotice((error as Error).message);
      setCommit(null);
    } finally {
      setBusy(false);
    }
  }

  async function spinWheel() {
    if (busy) return;
    if (!commit) {
      await prepareCommit();
      return;
    }
    setBusy(true);
    setNotice("");
    try {
      if (demoMode) {
        const rewards = [5, 10, 25, 50, 100, 500];
        const reward = rewards[Math.floor(Math.random() * rewards.length)];
        setDemoBalance((current) => current + reward);
        setLastResult({
          game_id: "demo-wheel",
          game: "wheel",
          balance: demoBalance + reward,
          server_seed_hash: commit.server_seed_hash,
          result: { reward, server_seed: "demo-seed" },
        });
      } else {
        const result = await playWheel(initData, commit);
        setLastResult(result);
        setBalance(result.balance);
      }
      setCommit(null);
    } catch (error) {
      setNotice((error as Error).message);
      setCommit(null);
    } finally {
      setBusy(false);
    }
  }

  async function buyPackage(packageKey: string) {
    if (demoMode) {
      setNotice("Оплата Telegram Stars отключена в демо-версии.");
      return;
    }
    setBusy(true);
    try {
      const { invoice_url } = await createInvoice(initData, packageKey);
      openTelegramInvoice(invoice_url, () => {
        getPlayer(initData).then((data) => {
          setPlayer(data);
          setBalance(data.balance);
        });
        setShopOpen(false);
      });
    } catch (error) {
      setNotice((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function adjustBet(delta: number) {
    setBet((current) => Math.max(10, Math.min(50_000, current + delta)));
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <button className="brand" onClick={() => selectMode("blackjack")} aria-label="МОЗГИНО — главная">
          <span className="brand-mark"><span>М</span></span>
          <span className="brand-word">МОЗГИНО<span className="brand-dot">.</span></span>
        </button>
        <div className="side-caption">ИГРОВОЙ ХАБ</div>
        <nav className="side-nav">
          <button className={`nav-item ${mode === "blackjack" ? "selected" : ""}`} onClick={() => selectMode("blackjack")}>
            <span className="nav-icon">▤</span><span>Блэкджек</span><span className="nav-live" />
          </button>
          <button className={`nav-item ${mode === "dice" ? "selected" : ""}`} onClick={() => selectMode("dice")}>
            <span className="nav-icon">⚄</span><span>Кубик</span>
          </button>
          <button className={`nav-item ${mode === "wheel" ? "selected" : ""}`} onClick={() => selectMode("wheel")}>
            <span className="nav-icon">◉</span><span>Колесо фортуны</span><span className="free-tag">FREE</span>
          </button>
        </nav>

        <div className="sidebar-bottom">
          <div className="fair-mini">
            <div className="fair-mini-icon">⌁</div>
            <div><strong>Честная игра</strong><span>Provably fair · SHA-256</span></div>
            <span className="green-dot" />
          </div>
          <button className="help-link" onClick={() => setNotice("Поддержка МОЗГИНО: напишите боту @mozгino_support.")}>
            <span>?</span> Помощь и правила
          </button>
          <div className="sidebar-disclaimer">18+ · Только виртуальные фишки</div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumb"><span>АРКАДА</span><i>/</i>{modeLabel.toUpperCase()}</div>
          <div className="top-actions">
            <div className="online-pill"><span className="green-dot" /> 1 284 <span className="online-label">в сети</span></div>
            <button className="balance-pill" onClick={() => setShopOpen(true)}>
              <span className="balance-symbol">✳</span>
              <span className="balance-amount">{format(visibleBalance)}</span>
              <span className="balance-label">NEU</span>
              <span className="balance-add">+</span>
            </button>
            <button className="profile-button" title={displayName}>{player?.avatar_url ? <img className="profile-image" src={player.avatar_url} alt="" /> : displayName.slice(0, 1).toUpperCase()}</button>
          </div>
        </header>

        {demoMode && (
          <div className="demo-banner"><span>◈</span> ДЕМО-РЕЖИМ <i /> Баланс и результаты здесь условные — платежи отключены</div>
        )}

        <div className="page-content">
          <section className="page-heading">
            <div>
              <div className="eyebrow"><span /> НЕЙРОННАЯ АРКАДА <span className="eyebrow-line" /></div>
              <h1>Играй на <em>импульсе.</em></h1>
              <p>Проверь интуицию. Поймай волну. Забери свой момент.</p>
            </div>
            <div className="heading-chip"><span className="chip-icon">⌘</span><span>СЕССИЯ<br /><b>#{(Date.now() % 9000 + 1000).toString().slice(0, 4)}</b></span></div>
          </section>

          <section className="game-layout">
            <div className={`game-board ${mode}`}>
              <div className="board-topline">
                <div className="game-title-lockup">
                  <span className="game-title-icon">{mode === "blackjack" ? "▤" : mode === "dice" ? "⚄" : "◉"}</span>
                  <div><h2>{modeLabel}</h2><span>{mode === "blackjack" ? "ЦЕЛЬ — 21 · ДИЛЕР: 17+" : mode === "dice" ? "ПОЙМАЙ ЧИСЛО — ЗАБЕРИ ВЫПЛАТУ" : "ОДИН БЕСПЛАТНЫЙ СПИН КАЖДЫЕ 24 ЧАСА"}</span></div>
                </div>
                <div className="table-status"><span /> LIVE TABLE</div>
              </div>

              {mode === "blackjack" && (
                <>
                  <div className="table-felt">
                    <div className="felt-grid" />
                    <div className="felt-glow" />
                    <div className="table-arc arc-one" /><div className="table-arc arc-two" />
                    <div className="table-center-mark"><span>М</span><small>NEURAL<br />21</small></div>
                    <div className="dealer-zone">
                      <div className="hand-label"><span className="dealer-avatar">♟</span><div><b>ДИЛЕР</b><small>{game?.status === "finished" ? `ОЧКИ · ${game.dealer_value}` : "СЛЕДИТ ЗА ИГРОЙ"}</small></div>
                        {game?.status === "finished" && <strong className="score">{game.dealer_value}</strong>}
                      </div>
                      <div className="cards dealer-cards">
                        {(game?.dealer?.length ? game.dealer : [{ hidden: true }, { hidden: true }]).map((card, index) => <PlayingCard key={`d-${game?.game_id ?? "empty"}-${index}`} card={card} />)}
                      </div>
                    </div>
                    <div className="table-divider"><span>NEURAL TABLE · NO. 021</span><i /><span>♧</span><i /><span>PROVABLY FAIR</span></div>
                    <div className="player-zone">
                      <div className="cards player-cards">
                        {(game?.hand?.length ? game.hand : [{ hidden: true }, { hidden: true }]).map((card, index) => <PlayingCard key={`p-${game?.game_id ?? "empty"}-${index}`} card={card} />)}
                      </div>
                      <div className="hand-label player-label"><span className="player-avatar">{displayName.slice(0, 1).toUpperCase()}</span><div><b>{displayName.toUpperCase()}</b><small>{game ? `ВАШИ ОЧКИ · ${game.hand_value}` : "ВАШ ХОД · УДАЧИ"}</small></div>
                        {game && <strong className="score player-score">{game.hand_value}</strong>}
                      </div>
                    </div>
                    {game?.status === "finished" && game.result && (
                      <div className={`round-result ${game.result.outcome}`}>
                        <span>{game.result.outcome === "player" ? "ПОБЕДА" : game.result.outcome === "push" ? "НИЧЬЯ" : "РАУНД ЗАВЕРШЁН"}</span>
                        <b>{game.result.net > 0 ? `+${format(game.result.net)}` : game.result.net === 0 ? "ВОЗВРАТ СТАВКИ" : `${format(game.result.net)}`} <small>NEU</small></b>
                      </div>
                    )}
                    {!game && <div className="table-hint">Сделай ставку и проверь свою интуицию</div>}
                  </div>
                  <div className="board-controls">
                    <div className="bet-control">
                      <div className="control-caption">ВАША СТАВКА <span>· НЕЙРОНЫ</span></div>
                      <div className="bet-stepper"><button onClick={() => adjustBet(-50)} disabled={!!game && game.status === "active"}>−</button><strong>{format(bet)}</strong><button onClick={() => adjustBet(50)} disabled={!!game && game.status === "active"}>+</button></div>
                      <div className="bet-presets">{[100, 200, 500, 1000].map((amount) => <button key={amount} className={bet === amount ? "active" : ""} onClick={() => setBet(amount)} disabled={!!game && game.status === "active"}>{amount}</button>)}</div>
                    </div>
                    <div className="play-controls">
                      {game?.status === "active" ? (
                        <>
                          <button className="action-button secondary" onClick={() => void doBlackjackAction("hit")} disabled={busy}>ВЗЯТЬ КАРТУ <span>↗</span></button>
                          <button className="action-button secondary" onClick={() => void doBlackjackAction("stand")} disabled={busy}>ХВАТИТ <span>Ⅱ</span></button>
                          <button className="action-button" onClick={() => void doBlackjackAction("double")} disabled={busy || !game.can_double}>УДВОИТЬ <span>×2</span></button>
                        </>
                      ) : (
                        <button className="action-button primary-action" onClick={() => void dealBlackjack()} disabled={busy}>
                          {busy ? <><span className="spinner" /> СЕКУНДУ…</> : commit ? "ПОДТВЕРДИТЬ СТАВКУ" : game ? "НОВАЯ РАЗДАЧА" : "НАЧАТЬ ИГРУ"}
                          {!busy && <span>↗</span>}
                        </button>
                      )}
                    </div>
                  </div>
                </>
              )}

              {mode === "dice" && (
                <div className="dice-game-panel">
                  <div className={`die-visual ${lastResult?.game === "dice" ? "rolled" : ""}`}><span>{lastResult?.game === "dice" ? diceFace(lastResult.result.roll ?? 1) : "⚄"}</span><small>{lastResult?.game === "dice" ? `ВЫПАЛО ${lastResult.result.roll}` : "БРОСАЙ"}</small></div>
                  <div className="dice-choice">
                    <div className="control-caption">ВЫБЕРИ СТАВКУ</div>
                    <div className="dice-mode-grid">
                      {([["exact", "Точное число", "×5.7"], ["odd", "Нечёт", "×1.9"], ["even", "Чёт", "×1.9"], ["low", "1 — 3", "×1.9"], ["high", "4 — 6", "×1.9"]] as [DiceMode, string, string][]).map(([key, label, multiplier]) => (
                        <button key={key} className={diceMode === key ? "selected" : ""} onClick={() => setDiceMode(key)}><span>{label}</span><b>{multiplier}</b></button>
                      ))}
                    </div>
                    {diceMode === "exact" && <div className="number-picks">{[1, 2, 3, 4, 5, 6].map((number) => <button key={number} className={dicePick === number ? "selected" : ""} onClick={() => setDicePick(number)}>{number}</button>)}</div>}
                    {lastResult?.game === "dice" && <div className={`dice-outcome ${lastResult.result.won ? "win" : ""}`}>{lastResult.result.won ? `ПОБЕДА · +${format(lastResult.result.net ?? 0)} NEU` : "НЕ В ЭТОТ РАЗ · ПОПРОБУЙ ЕЩЁ"}</div>}
                  </div>
                  <div className="dice-bottom">
                    <div className="bet-control"><div className="control-caption">СТАВКА · NEU</div><div className="bet-stepper"><button onClick={() => adjustBet(-50)}>−</button><strong>{format(bet)}</strong><button onClick={() => adjustBet(50)}>+</button></div></div>
                    <button className="action-button primary-action" onClick={() => void rollDice()} disabled={busy}>{busy ? "БРОСАЕМ…" : commit ? "ПОДТВЕРДИТЬ БРОСОК" : "БРОСИТЬ КУБИК"} <span>↗</span></button>
                  </div>
                </div>
              )}

              {mode === "wheel" && (
                <div className="wheel-game-panel">
                  <div className="wheel-copy"><div className="free-badge">✦ БЕСПЛАТНЫЙ СПИН</div><h3>Поймай волну<br /><em>удачи.</em></h3><p>Один импульс каждые 24 часа.<br />Никаких ставок — только бонусные нейроны.</p>
                    <button className="action-button primary-action" onClick={() => void spinWheel()} disabled={busy}>{busy ? "КОЛЕСО КРУТИТСЯ…" : commit ? "ПОДТВЕРДИТЬ СПИН" : "КРУТИТЬ КОЛЕСО"} <span>↗</span></button>
                    {lastResult?.game === "wheel" && <div className="wheel-win">+{format(lastResult.result.reward ?? 0)} <small>NEU ЗАЧИСЛЕНО</small></div>}
                  </div>
                  <div className="wheel-stage"><div className="wheel-pointer">▼</div><div className="wheel-ring"><div className="wheel-inner"><span>✳</span><small>МОЗГИНО</small></div><b className="wheel-label wl-1">5</b><b className="wheel-label wl-2">10</b><b className="wheel-label wl-3">25</b><b className="wheel-label wl-4">50</b><b className="wheel-label wl-5">100</b><b className="wheel-label wl-6">500</b></div></div>
                  <div className="wheel-footnote"><span className="green-dot" /> СЛЕДУЮЩИЙ СПИН ДОСТУПЕН ЧЕРЕЗ 24 ЧАСА</div>
                </div>
              )}
              {notice && <div className="notice">{notice}<button onClick={() => setNotice("")}>×</button></div>}
            </div>

            <aside className="info-column">
              <div className="info-card streak-card">
                <div className="card-kicker">ТВОЙ ИМПУЛЬС <span>↗</span></div>
                <div className="streak-number">03<span>×</span></div>
                <div className="streak-caption">ДНЯ В ИГРЕ</div>
                <div className="streak-chart"><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /></div>
                <div className="streak-bottom"><span>Серия поддерживает нерв</span><b>+12%</b></div>
              </div>
              <div className="info-card fairness-card">
                <div className="card-kicker">ПРОВЕРЬ ЧЕСТНОСТЬ <span>⌁</span></div>
                <div className="fairness-main"><span className="fairness-lock">⌑</span><div><b>Provably fair</b><small>Каждый исход можно проверить</small></div></div>
                <div className="seed-row"><span>SERVER SEED HASH</span><b>{fairnessHash ?? "ОЖИДАЕТ РАУНД"}</b></div>
                <div className="seed-row"><span>CLIENT SEED</span><b>{fairnessClientSeed ?? "АВТОГЕНЕРАЦИЯ"}</b></div>
                {revealedSeed && <div className="seed-row reveal"><span>SERVER SEED · REVEALED</span><b>{revealedSeed}</b></div>}
                <div className="fairness-footer"><span className="green-dot" /> SHA-256 · HMAC · ОТКРЫТЫЙ РЕЗУЛЬТАТ</div>
              </div>
              <button className="ai-card" onClick={() => void handleTrophy()} disabled={busy}>
                {(avatarAsset?.image_url ?? trophy?.image_url) ? <img className="ai-image" src={avatarAsset?.image_url ?? trophy?.image_url ?? ""} alt="AI-арт МОЗГИНО" /> : <><span className="ai-orbit orbit-a" /><span className="ai-orbit orbit-b" /><span className="ai-brain">✺</span></>}
                <span className="ai-copy"><small>AI CANVAS {avatarAsset?.status ? `· ${avatarAsset.status.toUpperCase()}` : trophy?.status ? `· ${trophy.status.toUpperCase()}` : ""}</small><b>{player?.avatar_credits ? <>Твой AI-аватар.<br />Один импульс.</> : avatarAsset?.image_url || trophy?.image_url ? <>Твой трофей<br />готов.</> : <>Твой выигрыш.<br />Твой трофей.</>}</b><i>{player?.avatar_credits ? "Сгенерировать аватар" : avatarAsset || trophy ? "Проверить генерацию" : "Создать трофей / аватар"} <span>↗</span></i></span>
              </button>
              <div className="mini-stats"><div><span>ИГР СЕГОДНЯ</span><b>2 491</b></div><i /><div><span>ВЫПЛАЧЕНО</span><b>₦ 84.2K</b></div></div>
            </aside>
          </section>

          <section className="bottom-strip">
            <div className="bottom-label"><span className="signal-icon">⌁</span><div><b>НЕЙРОНОВОСТИ</b><small>Последние импульсы аркады</small></div></div>
            <div className="ticker"><span className="ticker-green">●</span> <b>synapse_404</b> поймал 21 на последней карте <i>·</i> 2 мин назад <span className="ticker-divider" /><span className="ticker-purple">✦</span> <b>pixelbrain</b> забрал бонус колеса <i>·</i> 5 мин назад</div>
            <button className="ticker-more" onClick={() => setNotice("Лента активности появится в следующей итерации.")}>ВСЯ ЛЕНТА ↗</button>
          </section>
          <footer className="footer-note">МОЗГИНО — развлекательная игра. Нейроны являются виртуальными баллами и не имеют денежной стоимости. <span>ИГРАЙ ОТВЕТСТВЕННО</span></footer>
        </div>
      </main>

      {shopOpen && <div className="modal-backdrop" onClick={() => setShopOpen(false)}><div className="shop-modal" onClick={(event) => event.stopPropagation()}>
        <button className="modal-close" onClick={() => setShopOpen(false)}>×</button>
        <div className="eyebrow"><span /> НЕЙРОННЫЙ БУСТ</div><h2>Пополнить<br /><em>импульс.</em></h2>
        <p>Оплата Telegram Stars · XTR</p>
        {Object.entries(player?.packages ?? {
          starter: { title: "Стартовый импульс", stars: 50, neurons: 500 },
          neural: { title: "Нейроускоритель", stars: 150, neurons: 1650 },
          synapse: { title: "Синапс-пак", stars: 500, neurons: 6000 },
          avatar: { title: "AI-аватар", stars: 100, neurons: 0 },
        }).map(([key, pack]) => <button className="package-row" key={key} onClick={() => void buyPackage(key)}><span><b>{pack.title}</b><small>{key === "avatar" ? "Одна генерация портрета" : `✳ ${format(pack.neurons)} NEU`}</small></span><strong>{pack.stars} ⭐</strong></button>)}
        {(player?.avatar_credits ?? 0) > 0 && <div className="avatar-credit-note">✦ В профиле доступно генераций аватара: {player?.avatar_credits}</div>}
        <small className="modal-legal">Виртуальные баллы не подлежат обмену или выводу.</small>
      </div></div>}
    </div>
  );
}

function PlayingCard({ card }: { card: Card }) {
  if (card.hidden) return <div className="playing-card hidden-card"><span>М</span><i>✳</i></div>;
  const red = card.suit === "♥" || card.suit === "♦";
  return <div className={`playing-card ${red ? "red-card" : ""}`}><b>{card.rank}</b><span>{card.suit}</span><small>{card.rank}{card.suit}</small></div>;
}

function diceFace(value: number): string {
  return ["", "⚀", "⚁", "⚂", "⚃", "⚄", "⚅"][value] ?? "⚄";
}

export default App;
