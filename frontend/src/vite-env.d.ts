/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
  readonly VITE_DEMO_MODE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

interface TelegramWebApp {
  initData: string;
  initDataUnsafe?: {
    user?: { first_name?: string; username?: string };
  };
  ready(): void;
  expand(): void;
  setHeaderColor?(color: string): void;
  openInvoice?(
    url: string,
    callback?: (status: "paid" | "cancelled" | "failed" | "pending") => void,
  ): void;
}

interface Window {
  Telegram?: { WebApp?: TelegramWebApp };
}
