document.addEventListener('DOMContentLoaded', function () {
    // Ensure particles.js is fully loaded before initializing it
    if (typeof particlesJS !== 'undefined') {
      particlesJS("particles-js", {
        particles: {
          number: { value: 80 },
          color: { value: "#4ecca3" },
          shape: { type: "circle" },
          opacity: { value: 0.5 },
          size: { value: 3 },
          move: {
            enable: true,
            speed: 2,
            direction: "none",
            random: false,
            straight: false,
            out_mode: "out",
            bounce: false,
          },
        },
        interactivity: {
          detect_on: "canvas",
          events: {
            onhover: { enable: true, mode: "repulse" },
            onclick: { enable: true, mode: "push" },
            resize: true,
          },
        },
        retina_detect: true,       
      });
    } else {
      console.error("particlesJS is not defined");
    }
  });


// Search Bar Handler
const searchForm = document.getElementById('search-form');
if (searchForm) {
    searchForm.addEventListener('submit', function(e) {
        e.preventDefault();
        const searchInput = document.getElementById('search-input');
        const resultDiv = document.getElementById('search-result');
        if (!searchInput || !resultDiv) {
            console.error("Search input or result div not found.");
            return;
        }

        if (!searchInput.value.trim()) {
            resultDiv.innerHTML = '<div class="alert alert-danger glass-card">Please enter a URL or search term</div>';
            addLogEntry("Search error: No input provided", "error");
            return;
        }

        resultDiv.innerHTML = '<div class="spinner-border text-light" role="status"><span class="visually-hidden">Loading...</span></div>';

        const formData = new FormData();
        formData.append('search_input', searchInput.value.trim());

        fetch('/search/', {
            method: 'POST',
            headers: {
                'X-CSRFToken': window.csrfToken
            },
            body: formData
        })
            .then(response => {
                if (!response.ok) throw new Error(`HTTP error! Status: ${response.status}`);
                return response.json();
            })
            .then(data => {
                if (data.success) {
                    resultDiv.innerHTML = `
                        <div class="alert alert-success glass-card">
                            <strong>Saved!</strong><br>
                            Domain: ${data.domain}<br>
                            URL: ${data.url}
                        </div>`;
                    addLogEntry(`Saved ${data.domain} and ${data.url}`, "success");
                } else {
                    resultDiv.innerHTML = `<div class="alert alert-danger glass-card">${data.error}</div>`;
                    addLogEntry(`Search error: ${data.error}`, "error");
                }
            })
            .catch(error => {
                console.error('Error during search:', error);
                resultDiv.innerHTML = '<div class="alert alert-danger glass-card">An error occurred during search</div>';
                addLogEntry("Search failed", "error");
            });
    });
} else {
    console.error("Search form not found.");
}

  // Log Management
  function addLogEntry(message, type = "info") {
    const icons = {
      info: "info-circle",
      warning: "exclamation-triangle",
      error: "radiation",
      success: "check-circle",
    };
  
    const logEntry = document.createElement("div");
    logEntry.className = `log-entry ${type}-entry`;
    logEntry.innerHTML = `
      <i class="fas fa-${icons[type]}"></i>
      <span class="log-message">${message}</span>
      <span class="log-time">${new Date().toLocaleTimeString()}</span>
    `;
  
    const logTrack = document.querySelector(".log-track");
    logTrack.appendChild(logEntry);
  
    // Auto-scroll and auto-remove
    setTimeout(() => (logTrack.scrollTop = logTrack.scrollHeight), 100);
    setTimeout(() => {
      logEntry.style.transform = "translateX(-100%)";
      logEntry.style.opacity = "0";
      setTimeout(() => logEntry.remove(), 500);
    }, 10000);
  }
  
  // Crawler Control Logic
  const runBtn = document.getElementById("run-crawler-btn");
  const stopBtn = document.getElementById("stop-crawler-btn");
  
  let crawlerInterval; // Declare this variable outside any function
  
  
  function updateCrawlerStatus() {
    fetch(getCrawlerStateUrl)
      .then((response) => response.json())
      .then((data) => {
        if (data.is_running) {
          runBtn.style.display = "none";
          stopBtn.style.display = "inline-block";
          startLogPolling();
        } else {
          stopBtn.style.display = "none";
          runBtn.style.display = "inline-block";
          if (crawlerInterval) clearInterval(crawlerInterval);
        }
      });
  }
  
  function startLogPolling() {
    crawlerInterval = setInterval(() => {
      fetch(getLogsUrl)
        .then((response) => response.json())
        .then((data) => {
          data.logs.forEach((log) => {
            const type = log.includes("ERROR")
              ? "error"
              : log.includes("WARNING")
              ? "warning"
              : log.includes("SUCCESS")
              ? "success"
              : "info";
            addLogEntry(log, type);
          });
        });
    }, 1500);
  }
  
  // Event Listeners
  runBtn.addEventListener("click", () => {
    addLogEntry("Initializing crawler system...", "info");
    fetch(runCrawlerUrl)
      .then((response) => response.json())
      .then((data) => {
        if (data.status === "started") {
          addLogEntry("Crawler launched successfully", "success");
          updateCrawlerStatus();
        }
      });
  });
  
  stopBtn.addEventListener("click", () => {
    addLogEntry("Initiating emergency stop procedure...", "warning");
    fetch(stopCrawlerUrl).then(() => {
      addLogEntry("Crawler system halted", "error");
      updateCrawlerStatus();
    });
  });
  
  // Initial status check
  updateCrawlerStatus();
  

  