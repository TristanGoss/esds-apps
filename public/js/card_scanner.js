
const allDiv = document.getElementById("all");
const resultDiv = document.getElementById("result");
const scanner = new Html5Qrcode("qr-reader");
const sounds = {
  valid: new Audio("/public/330047__paulmorek__beep-04-positive-2.mp3"),
  invalid: new Audio("/public/330049__paulmorek__beep-04-negative.mp3")
};
let isScannerRunning = false;
let readyForScan = true;
let lastDecodedText = null;

// Force the user to click once to enable the scanner.
// This works around the issue where iOS won't allow
// audio to play until the user has interacted with the page.
document.addEventListener("click", () => {
  if (isScannerRunning) return;

  // buffer the audio so that it plays correctly on the first scan
  [sounds.valid, sounds.invalid].forEach(sound => {
    sound.volume = 0;
    sound.play().then(() => {
      sound.pause();
      sound.currentTime = 0;
      sound.volume = 1;
    }).catch(() => {});
  });

  // start the scanner
  scanner.start(
    { facingMode: "environment" },
    {
      fps: 10,
      qrbox: { width: 1000, height: 1000 }
    },
    (decodedText, decodedResult) => {
      if (!readyForScan || decodedText === lastDecodedText) return;
      readyForScan = false; // prevent repeated scans until no QR code is detected.
      lastDecodedText = decodedText
      validateCard(decodedText).then(() => {
          setTimeout(() => {lastDecodedText = null}, 4000) // debounce repeated scans of the same code
      });
    },
    (errorMessage) => {
      // If nothing is detected (i.e. the code has been removed from view) we may ready for another scan
      readyForScan = true;
    }
  ).then(() => {
      // Hide the help text once scanner is running
      document.getElementById("qr-help").classList.add("hidden");
  isScannerRunning = true;
  })
}, { once: true });

function showResult(message, isValid) {
  resultDiv.textContent = message;
  resultDiv.style.color = isValid ? "green" : "red";

  // Flash scanner background
  const flashClass = isValid ? "flash-green" : "flash-red";
  allDiv.classList.add(flashClass);
  setTimeout(() => allDiv.classList.remove(flashClass), 600); // flash for 600ms

  // Play sound
  const audioResponse = isValid ? sounds.valid : sounds.invalid;
  audioResponse.currentTime = 0;  // reset playback in case sound was already played
  audioResponse.play().catch(() => {});
}

async function validateCard(data) {
  try {
    const res = await fetch(`/proxy-card-check?url=${encodeURIComponent(data)}`);
    const text = await res.text();
    const valid = text.includes("This membership card is valid");
    showResult(valid ? "✅ CARD VALID" : "❌ CARD INVALID", valid);
  } catch (err) {
    showResult("❌ FAILED TO VALIDATE", false);
  }
}
