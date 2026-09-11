document.addEventListener("DOMContentLoaded", () => {
    const inputField = document.getElementById("userInput");
    const sendBtn = document.getElementById("sendBtn");
    const chatContainer = document.getElementById("chatMessages");
    const newChatBtn = document.getElementById("newChatBtn");

    let currentSessionId = localStorage.getItem("current_session_id") || null;
    let isProcessing = false;

    // Helper to render Markdown + KaTeX LaTeX Equations safely
    function renderFormattedContent(element, text) {
        element.innerHTML = window.marked ? marked.parse(text) : text;
        
        if (window.renderMathInElement) {
            renderMathInElement(element, {
                delimiters: [
                    {left: '$$', right: '$$', display: true},
                    {left: '$', right: '$', display: false},
                    {left: '\\(', right: '\\)', display: false},
                    {left: '\\[', right: '\\]', display: true}
                ],
                throwOnError: false
            });
        }
    }

    // Typewriter effect compatible with Markdown and KaTeX math expressions
    function typeWriterHTML(element, fullText, speed = 8, onComplete = null) {
        let i = 0;
        element.innerHTML = "";
        
        function type() {
            if (i < fullText.length) {
                // Advance typing index
                i += 2; 
                if (i > fullText.length) i = fullText.length;
                
                const currentChunk = fullText.slice(0, i);
                renderFormattedContent(element, currentChunk);
                if (chatContainer) chatContainer.scrollTop = chatContainer.scrollHeight;
                setTimeout(type, speed);
            } else {
                renderFormattedContent(element, fullText);
                if (chatContainer) chatContainer.scrollTop = chatContainer.scrollHeight;
                if (onComplete) onComplete();
            }
        }
        type();
    }

    function lockUI() {
        isProcessing = true;
        if (sendBtn) {
            sendBtn.disabled = true;
            sendBtn.innerText = "Thinking...";
        }
        if (inputField) inputField.disabled = true;
    }

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

    async function loadSidebarThreads() {
        try {
            const res = await fetch("/get_threads");
            if (!res.ok) return;

            const data = await res.json();
            const sidebarContainer = document.getElementById("threadsList") || document.querySelector(".recent-chats");

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

    async function loadThreadMessages(sessionId) {
        if (isProcessing || !chatContainer) return;

        currentSessionId = sessionId;
        localStorage.setItem("current_session_id", sessionId);
        chatContainer.innerHTML = "";

        try {
            const res = await fetch(`/get_thread_messages/${sessionId}`);
            if (!res.ok) {
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
                        renderFormattedContent(botBubble, msg.bot);
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
        typeWriterHTML(botBubble, text, 8, callback);
    }

    function renderErrorBubble(errorText) {
        if (!chatContainer) return;
        const errorBubble = document.createElement("div");
        errorBubble.className = "message bot error";
        errorBubble.textContent = errorText;
        chatContainer.appendChild(errorBubble);
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }

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
                localStorage.setItem("current_session_id", data.session_id);
            }

            const replyText = data.reply || "No response received.";

            renderBotBubbleAnimated(replyText, () => {
                unlockUI();
                loadSidebarThreads();
            });
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
            localStorage.removeItem("current_session_id");
            if (chatContainer) {
                chatContainer.innerHTML = '<div class="message bot">Welcome to ADISE Chatbot! Ask ADISE anything you want to know.</div>';
            }
        });
    }

    if (chatContainer) {
        loadSidebarThreads();
    }
});
