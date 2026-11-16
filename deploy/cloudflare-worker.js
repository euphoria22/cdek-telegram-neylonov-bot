/**
 * Cloudflare Worker — прокси к Telegram Bot API для серверов, где api.telegram.org недоступен.
 *
 * Установка: dash.cloudflare.com → Workers & Pages → Create → Create Worker →
 * Deploy → Edit code → вставить этот файл целиком → Deploy.
 * Адрес вида https://<имя>.<аккаунт>.workers.dev указать в .env бота: TELEGRAM_API_URL=...
 *
 * Чтобы прокси не мог использовать кто угодно, впишите ID своего бота —
 * это число до двоеточия в токене (токен 123456789:AA... → ID 123456789).
 */
const ALLOWED_BOT_ID = ""; // например "123456789"; пусто — без ограничения

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const m = url.pathname.match(/^\/(?:file\/)?bot(\d+):/);
    if (!m || (ALLOWED_BOT_ID && m[1] !== ALLOWED_BOT_ID)) {
      return new Response("Not found", { status: 404 });
    }
    url.protocol = "https:";
    url.hostname = "api.telegram.org";
    url.port = "";
    return fetch(new Request(url.toString(), request));
  },
};
