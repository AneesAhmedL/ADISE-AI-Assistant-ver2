document.addEventListener("DOMContentLoaded", () => {
    const inputField = document.getElementById("userInput");
    const sendBtn = document.getElementById("sendBtn");
    const chatContainer = document.getElementById("chatMessages");
    const newChatBtn = document.getElementById("newChatBtn");

    let currentSessionId = null;
    let isProcessing = false;

    // HTML-aware Typewriter Animation
    function typeWriterHTML(element, fullText, speed = 10, onComplete = null) {
        let i = 0;
        element.innerHTML = "";
        
        function type() {
            if (i < fullText.length) {
                if (fullText.charAt(i) === "<") {
                    const tagEnd = fullText.indexOf(">", i);
                    if (tagEnd !== -1) {
                        i = tagEnd + 1;
                    } else {
                        i++;
                    }
                } else {
                    i++;
                }
                
                const currentChunk = fullText.slice(0, i);
                element.innerHTML = window.marked ? marked.parse(currentChunk) : currentChunk;
                if (chatContainer) chatContainer.scrollTop = chatContainer.scrollHeight;
                setTimeout(type, speed);
            } else {
                element.innerHTML = window.marked ? marked.parse(fullText) : fullText;
                if (chatContainer) chatContainer.scrollTop = chatContainer.scrollHeight;
                
                if (onComplete) onComplete();
            }
        }
        type();
    }

    // Lock UI controls while waiting or typing
    function lockUI() {
        isProcessing = true;
        if (sendBtn) {
            sendBtn.disabled = true;
            sendBtn.innerText = "Thinking...";
        }
        if (inputField) inputField.disabled = true;
    }

    // Unlock UI controls after completion
    function unlockUI() {
        isProcessing = false;
        if (sendBtn) {
            sendBtn.disabled = false;
            sendBtn.innerText = "Send";
        }
        if (inputField) {
            inputField.disabled = false;
            inputField.focus();
        }
    }

    // Fetch and populate sidebar chat threads
    async function loadSidebarThreads() {
        try {
            const res = await fetch("/get_threads");
            if (!res.ok) return;

            const data = await res.json();
            const sidebarContainer = document.querySelector(".recent-chats") || document.getElementById("recentChats");

            if (sidebarContainer && data.threads) {
                sidebarContainer.innerHTML = "";
                data.threads.forEach(thread => {
                    const btn = document.createElement("button");
                    btn.className = "chat-item-btn";
                    btn.textContent = thread.title;
                    btn.onclick = () => loadThreadMessages(thread.session_id);
                    sidebarContainer.appendChild(btn);
                });
            }
        } catch (err) {
            console.error("Failed to load threads:", err);
        }
    }

    // Load conversation when clicking a sidebar thread item
    async function loadThreadMessages(sessionId) {
        if (isProcessing || !chatContainer) return;

        currentSessionId = sessionId;
        chatContainer.innerHTML = "";

        try {
            const res = await fetch(`/get_thread_messages/${sessionId}`);

            if (!res.ok) {
                console.error(`HTTP error! status: ${res.status}`);
                renderErrorBubble("Failed to retrieve chat history.");
                return;
            }

            const data = await res.json();

            if (data.messages && data.messages.length > 0) {
                data.messages.forEach(msg => {
                    if (msg.user) {
                        renderUserBubble(msg.user);
                    }

                    if (msg.bot) {
                        const botBubble = document.createElement("div");
                        botBubble.className = "message bot";
                        botBubble.innerHTML = window.marked ? marked.parse(msg.bot) : msg.bot;
                        chatContainer.appendChild(botBubble);
                    }
                });
                chatContainer.scrollTop = chatContainer.scrollHeight;
            } else {
                const emptyNotice = document.createElement("div");
                emptyNotice.className = "message bot";
                emptyNotice.textContent = "No messages saved in this thread yet.";
                chatContainer.appendChild(emptyNotice);
            }
        } catch (err) {
            console.error("Fetch error:", err);
            renderErrorBubble("Failed to retrieve chat history.");
        }
    }

    function renderUserBubble(text) {
        if (!chatContainer) return;
        const userBubble = document.createElement("div");
        userBubble.className = "message user";
        userBubble.textContent = text;
        chatContainer.appendChild(userBubble);
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }

    function renderBotBubbleAnimated(text, callback) {
        if (!chatContainer) return;
        const botBubble = document.createElement("div");
        botBubble.className = "message bot";
        chatContainer.appendChild(botBubble);
        typeWriterHTML(botBubble, text, 10, callback);
    }

    function renderErrorBubble(errorText) {
        if (!chatContainer) return;
        const errorBubble = document.createElement("div");
        errorBubble.className = "message bot error";
        errorBubble.textContent = errorText;
        chatContainer.appendChild(errorBubble);
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }

    // Main Send Message Function
    async function sendMessage() {
        if (isProcessing || !inputField) return;

        const message = inputField.value.trim();
        if (!message) return;

        lockUI();

        renderUserBubble(message);
        inputField.value = "";

        try {
            const res = await fetch("/chat", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ 
                    message: message,
                    session_id: currentSessionId 
                })
            });

            const data = await res.json();

            if (data.session_id) {
                currentSessionId = data.session_id;
            }

            const replyText = data.reply || "No response received.";

            renderBotBubbleAnimated(replyText, () => {
                unlockUI();
                loadSidebarThreads();
            });

            if (data.action === "open_url" && data.url) {
                window.open(data.url, "_blank");
            } else if (data.action === "logout") {
                setTimeout(() => window.location.href = "/", 1000);
            }
        } catch (err) {
            renderErrorBubble("Unable to communicate with the server.");
            unlockUI();
        }
    }

    if (sendBtn) {
        sendBtn.addEventListener("click", sendMessage);
    }

    if (inputField) {
        inputField.addEventListener("keypress", (e) => {
            if (e.key === "Enter" && !isProcessing) {
                sendMessage();
            }
        });
    }

    if (newChatBtn) {
        newChatBtn.addEventListener("click", () => {
            if (isProcessing) return;
            currentSessionId = null;
            if (chatContainer) chatContainer.innerHTML = "";
        });
    }

    // Initialize sidebar threads if chat element exists
    if (chatContainer) {
        loadSidebarThreads();
    }
});
