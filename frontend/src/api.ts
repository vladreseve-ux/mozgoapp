// Empty means same-origin. Vite proxies /api locally, and FastAPI serves the
// production build on the same origin for a one-URL local Telegram tunnel.
const API_URL = import.meta.env.VITE_API_URL ?? "";

export interface Player {
  user_id: number;
  first_name: string;
  username?: string;
  balance: number;
  avatar_credits: number;
  avatar_url: string | null;
  packages: Record<string, { title: string; stars: number; neurons: number }>;
}

export interface Card {
  rank?: string;
  suit?: string;
  hidden?: boolean;
}

export interface FairCommit {
  commit_id: string;
  client_seed: string;
  server_seed_hash: string;
}

export interface BlackjackState {
  game_id: string;
  game: "blackjack";
  status: "active" | "finished";
  bet: number;
  hand: Card[];
  dealer: Card[];
  hand_value: number;
  dealer_value: number | null;
  can_double: boolean;
  server_seed_hash: string;
  result?: {
    outcome: "player" | "dealer" | "push";
    payout: number;
    net: number;
    server_seed: string;
    client_seed: string;
    nonce: number;
  };
  balance?: number;
}

export interface ApiGameResult {
  game_id: string;
  game: "dice" | "wheel";
  balance: number;
  server_seed_hash: string;
  result: {
    roll?: number;
    mode?: string;
    pick?: number | null;
    won?: boolean;
    reward?: number;
    payout?: number;
    net?: number;
    server_seed: string;
    client_seed?: string;
    nonce?: number;
  };
}

export interface TrophyAsset {
  prediction_id: string;
  status: string;
  image_url: string | null;
  avatar_credits?: number;
}

async function api<T>(path: string, initData: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Telegram-Init-Data": initData,
      ...init?.headers,
    },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body?.detail;
    const message =
      typeof detail === "string" ? detail : detail?.message ?? "Не удалось связаться с сервером";
    throw new Error(message);
  }
  return body as T;
}

export const getPlayer = (initData: string) => api<Player>("/api/me", initData);

export const createFairCommit = (initData: string): Promise<FairCommit> => {
  const clientSeed = crypto.randomUUID();
  return api<{ commit_id: string; server_seed_hash: string }>("/api/fair/commit", initData, {
    method: "POST",
    body: JSON.stringify({ client_seed: clientSeed }),
  }).then((commit) => ({ ...commit, client_seed: clientSeed }));
};

export const startBlackjack = (
  initData: string,
  bet: number,
  commit: FairCommit,
): Promise<BlackjackState> =>
  api("/api/games/blackjack/start", initData, {
    method: "POST",
    body: JSON.stringify({ bet, commit_id: commit.commit_id, client_seed: commit.client_seed }),
  });

export const blackjackAction = (
  initData: string,
  gameId: string,
  action: "hit" | "stand" | "double",
): Promise<BlackjackState> =>
  api(`/api/games/blackjack/${gameId}/action`, initData, {
    method: "POST",
    body: JSON.stringify({ action, action_id: crypto.randomUUID() }),
  });

export const playDice = (
  initData: string,
  bet: number,
  mode: string,
  pick: number | undefined,
  commit: FairCommit,
): Promise<ApiGameResult> =>
  api("/api/games/dice", initData, {
    method: "POST",
    body: JSON.stringify({
      bet,
      mode,
      pick,
      commit_id: commit.commit_id,
      client_seed: commit.client_seed,
    }),
  });

export const playWheel = (initData: string, commit: FairCommit): Promise<ApiGameResult> =>
  api("/api/games/wheel", initData, {
    method: "POST",
    body: JSON.stringify({ commit_id: commit.commit_id, client_seed: commit.client_seed }),
  });

export const createInvoice = (initData: string, packageKey: string) =>
  api<{ invoice_url: string }>("/api/billing/invoice", initData, {
    method: "POST",
    body: JSON.stringify({ package_key: packageKey }),
  });

export const generateTrophy = (initData: string, gameId: string) =>
  api<TrophyAsset>("/api/ai/trophy", initData, {
    method: "POST",
    body: JSON.stringify({ game_id: gameId }),
  });

export const getTrophyStatus = (initData: string, predictionId: string) =>
  api<TrophyAsset>(`/api/ai/assets/${encodeURIComponent(predictionId)}`, initData);

export const generateAvatar = (initData: string) =>
  api<TrophyAsset>("/api/ai/avatar", initData, { method: "POST" });
