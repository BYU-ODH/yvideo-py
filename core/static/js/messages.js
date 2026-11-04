function ageMessages() {
  const messages = document.querySelectorAll("#django-messages-container .django-message");
  const messagesToBeExpired = [];
  for (const message of messages) {
    const age = Number(message.dataset.messageAge);

    if (message.matches(":hover")) {
      if (age < 3) {
        message.dataset.messageAge = "3";
      }
      continue;
    }

    if (!Number.isFinite(age)) {
      messagesToBeExpired.push(message);
      continue;
    }

    const remainingAge = age - 1;
    message.dataset.messageAge = remainingAge;
    if (remainingAge <= 0) {
      messagesToBeExpired.push(message);
      continue;
    }
  }

  for (let index = messagesToBeExpired.length - 1; index > -1; index--) {
    const message = messagesToBeExpired[index];
    if (message) {
      expireMessage(message);
    }
  }
}

function expireMessage(message) {
  message.remove();
}

function setupMessagesWatchers() {
  const messagesContainer = document.getElementById("django-messages-container");
  if (!messagesContainer) {
    return;
  }
  messagesContainer.addEventListener("click", (event) => {
    const removeButton = event.target.closest(".django-message-remove");
    if (!removeButton) {
      return;
    }

    const message = event.target.closest(".django-message");
    if (message) {
      event.preventDefault();
      expireMessage(message);
    }
  });

  function tick() {
    ageMessages();
    setTimeout(tick, 1000);
  }

  setTimeout(tick, 1000);
}

setupMessagesWatchers();
