const express = require('express');
const { formatMoney } = require('./common/format');

const app = express();

function healthHandler(req, res) {
  res.json({ status: 'ok' });
}

app.get('/health', healthHandler);
app.post('/legacy/orders/:id/cancel', (req, res) => {
  res.status(204).end();
});

module.exports = { app };
