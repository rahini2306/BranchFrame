'use strict';
const params = new URLSearchParams(location.hash.slice(1)),
  mode = params.get('mode'),
  token = params.get('token');
history.replaceState(null, '', location.pathname);
const message = document.getElementById('account-message'),
  form = document.getElementById('account-form');
const reset = mode === 'reset';
document.getElementById('account-title').textContent = reset ? 'A fresh start.' : 'Verify your email.';
document.getElementById('reset-label').hidden = !reset;
document.getElementById('confirm-label').hidden = !reset;
document.getElementById('new-password').required = reset;
document.getElementById('confirm-password').required = reset;
if (!token || !['reset', 'verify'].includes(mode)) {
  message.textContent = 'This link is incomplete. Request a new email from the studio.';
  form.hidden = true;
} else message.textContent = reset ? 'Choose a password with at least 10 characters.' : 'Confirm that this email address belongs to you.';
form.onsubmit = async event => {
  event.preventDefault();
  const password = document.getElementById('new-password').value;
  if (reset && password !== document.getElementById('confirm-password').value) {
    message.textContent = 'The passwords do not match.';
    return;
  }
  const button = document.getElementById('account-submit');
  button.disabled = true;
  try {
    const response = await fetch(reset ? '/api/reset-password' : '/api/verify-email', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        token,
        password
      })
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    message.textContent = result.message;
    form.hidden = true;
  } catch (error) {
    message.textContent = error.message;
    button.disabled = false;
  }
};
