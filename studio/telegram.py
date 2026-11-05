"""Bot Telegram : les validations depuis le téléphone."""
import html

import httpx

from . import db
from .config import Settings


class Bot:
    def __init__(self, settings: Settings, http=None):
        self.settings = settings
        self.http = http or httpx.Client(timeout=40)
        self.base = f"https://api.telegram.org/bot{settings.telegram_bot_token}"

    def send(self, text: str, buttons: list[list[dict]] | None = None, chat_id: str | None = None) -> None:
        body = {"chat_id": chat_id or self.settings.telegram_chat_id, "text": text[:4000], "parse_mode": "HTML",
                "disable_web_page_preview": True}
        if buttons:
            body["reply_markup"] = {"inline_keyboard": buttons}
        response = self.http.post(f"{self.base}/sendMessage", json=body)
        if response.status_code >= 400:
            db.log(f"Telegram : {response.text[:200]}", "warning")

    def updates(self, wait: int = 25) -> list[dict]:
        offset = db.get_setting("telegram_offset", 0)
        response = self.http.get(f"{self.base}/getUpdates", params={"offset": offset, "timeout": wait,
                                                                     "allowed_updates": '["callback_query"]'})
        response.raise_for_status()
        items = response.json()["result"]
        if items:
            db.set_setting("telegram_offset", items[-1]["update_id"] + 1)
        return items

    def answer(self, callback_id: str, text: str) -> None:
        self.http.post(f"{self.base}/answerCallbackQuery", json={"callback_query_id": callback_id, "text": text})

    def clear_buttons(self, chat_id, message_id) -> None:
        self.http.post(f"{self.base}/editMessageReplyMarkup", json={
            "chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}})


def proposal_message(settings: Settings, proposal_id: int, video: dict, payload: dict) -> tuple[str, list[list[dict]]]:
    lines = [f"<b>Nouvelle proposition</b>", f"Vidéo : {html.escape(video['title'])}", ""]
    lines += [f"<b>{i}.</b> {html.escape(t)}" for i, t in enumerate(payload["titles"], 1)]
    lines += ["", f"{len(payload['chapters'])} chapitres, {len(payload['tags'])} tags, {len(payload['shorts'])} Shorts, article prêt."]
    lines.append(f'<a href="{settings.public_url}/#valider/{proposal_id}">Voir tout le détail</a>')
    buttons = [[{"text": f"Titre {i}", "callback_data": f"ok:{proposal_id}:{i - 1}"}
                for i in range(1, len(payload["titles"]) + 1)],
               [{"text": "Autre proposition", "callback_data": f"re:{proposal_id}"},
                {"text": "Rejeter", "callback_data": f"no:{proposal_id}"}]]
    return "\n".join(lines), buttons
