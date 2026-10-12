import { init } from "@tma.js/sdk";

export function initializeTelegram(): string {
  try {
    init();
  } catch (error) {
    // SDK init is expected to be unavailable in a regular browser preview.
    console.info("Telegram SDK is running in browser preview mode.", error);
  }

  const webApp = window.Telegram?.WebApp;
  webApp?.ready();
  webApp?.expand();
  webApp?.setHeaderColor?.("#0a0712");
  return webApp?.initData ?? "";
}

export function openTelegramInvoice(url: string, onPaid: () => void): void {
  const webApp = window.Telegram?.WebApp;
  if (!webApp?.openInvoice) {
    window.open(url, "_blank", "noopener,noreferrer");
    return;
  }
  webApp.openInvoice(url, (status) => {
    if (status === "paid") onPaid();
  });
}
