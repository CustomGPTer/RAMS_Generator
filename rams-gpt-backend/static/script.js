'use strict';

const chatContainer = document.getElementById('chat-container');
const chatForm = document.getElementById('chat-form');
const messageInput = document.getElementById('message-input');

function addMessage(text, role) {
  const msgDiv = document.createElement('div');
  msgDiv.className = role + ' message';
  msgDiv.textContent = text;
  chatContainer.appendChild(msgDiv);
  chatContainer.scrollTop = chatContainer.scrollHeight;
  return msgDiv;
}

chatForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const userText = messageInput.value.trim();
  if (!userText) return;
  // Append the user's message to the chat display
  addMessage(userText, 'user');
  messageInput.value = '';
  // Show a placeholder for the assistant's response
  const placeholder = addMessage('Generating tailored response...', 'assistant');
  try {
    const response = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: userText }),
      credentials: 'same-origin'
    });
    if (!response.ok) {
      placeholder.textContent = 'Error: ' + response.status;
      return;
    }
    const data = await response.json();
    if (data.answer) {
      // Replace placeholder text with the actual answer from the assistant
      placeholder.textContent = data.answer;
    }
    if (data.done) {
      // If final document is ready, display a download link and disable further input
      const link = document.createElement('a');
      link.href = '/download';
      link.textContent = 'Download RAMS Document';
      link.className = 'download-link';
      chatContainer.appendChild(link);
      chatContainer.scrollTop = chatContainer.scrollHeight;
      messageInput.disabled = true;
      messageInput.placeholder = 'RAMS document ready.';
    }
  } catch (err) {
    placeholder.textContent = 'Error: ' + err.message;
  }
});

