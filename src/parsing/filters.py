def is_text_message(msg) -> bool:
    """
    Проверяет, является ли сообщение текстом (без фото, видео и т. д.).
    msg — объект Message из Pyrogram.
    """
    if not msg:
        return False
    if msg.service:  # служебное сообщение (вступления, закрепы и т.п.)
        return False
    if not getattr(msg, "text", None):
        return False
    return True
