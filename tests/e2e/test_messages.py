from playwright.sync_api import expect
import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.django_db(transaction=True),
]


@pytest.mark.parametrize("level", ["debug", "info", "success", "warning", "error"])
def test_display_user_message_appends_preserves_and_dismisses(
    logged_in_page, live_server, level
):
    page = logged_in_page
    page.goto(f"{live_server.url}/about/")
    container = page.locator("#django-messages-container")
    messages = container.locator(".django-message")
    expect(messages).to_have_count(0)

    page.evaluate(
        """async () => {
            const { displayUserMessage } = await import('/static/js/utils.js');
            await displayUserMessage('info', 'Existing message');
        }"""
    )
    expect(messages).to_have_count(1)
    existing_message = messages.filter(has_text="Existing message")

    text = f"New {level} message"
    page.evaluate(
        """async ({ level, text }) => {
            const { displayUserMessage } = await import('/static/js/utils.js');
            await displayUserMessage(level, text);
        }""",
        {"level": level, "text": text},
    )

    expect(messages).to_have_count(2)
    expect(existing_message.locator(".django-message-text")).to_have_text(
        "Existing message"
    )
    message = messages.filter(has_text=text)
    expect(message).to_have_count(1)
    expect(message).to_have_class(f"django-message {level}")
    expect(message.locator(".django-message-text")).to_have_text(text)
    label = message.locator(".django-message-status")
    expect(label).to_have_text(level.capitalize())
    expect(label).to_be_visible()

    message.hover()
    message.get_by_role("button", name="Close this message").click()
    expect(message).to_have_count(0)
    expect(messages).to_have_count(1)
    expect(existing_message.locator(".django-message-text")).to_have_text(
        "Existing message"
    )
