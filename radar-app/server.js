const express = require('express');
const app = express();
const http = require('http').createServer(app);
const io = require('socket.io')(http);

// Serve static files from the 'public' folder
app.use(express.static('public'));

// For development: Simulate incoming SDR data
// (Later you'll replace this with real data from your SDR script)
function simulateSDRData() {
  return {
    degree: Math.random() * 360,          // 0–360° azimuth
    amplitude: Math.random() * 100        // 0–100 signal strength
  };
}

// Broadcast new data every 1000ms (adjust as needed)
setInterval(() => {
  const data = simulateSDRData();
  io.emit('radar-data', data);
  console.log('Emitted:', data); // Helpful for debugging
}, 2000);

// Start the server
const PORT = 3000;
http.listen(PORT, () => {
  console.log(`Radar app running at http://localhost:${PORT}`);
});