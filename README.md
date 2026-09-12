# ADISE AI Assistant 🤖

**ADISE** is a secure, full-stack AI-powered personal assistant web application built with Flask, MongoDB Atlas, and a robust multi-tier AI failover architecture. It features secure user authentication with email OTP verification, dynamic persistent chat history, and seamless cloud deployment readiness.

🔗 **Live Demo:** [adise-ai-assistant-ver2.onrender.com](https://adise-ai-assistant-ver2.onrender.com)

---

## 🚀 Key Features

* **Multi-Tier AI Failover Engine:** Automatically switches between Primary and Secondary Google Gemini keys (`gemini-3.6-flash`), with an automatic backup fallback to the Hugging Face serverless router (`meta-llama/Llama-3.1-8B-Instruct`) during high traffic or rate limits.
* **Secure Authentication:** Complete user registration and login workflow backed by secure password hashing (`werkzeug.security`) and email verification via Brevo SMTP.
* **Persistent Chat History:** Full MongoDB Atlas integration to dynamically store, organize, and retrieve separate chat threads and session histories.
* **Production Ready:** Configured with `gunicorn`, strict security headers, and production deployment configuration for cloud hosting platforms like Render.

---

## 🛠️ Tech Stack

| Category | Technology |
| :--- | :--- |
| **Backend** | Python, Flask, Gunicorn |
| **Database** | MongoDB Atlas (via PyMongo) |
| **AI Integration** | Google GenAI SDK & Hugging Face Inference API |
| **Email Service** | Brevo API (SMTP) |
| **Security** | Werkzeug security, python-dotenv, secure session cookies |

---

## 👨‍💻 Author
It Was Created By
**ANEES AHMED L**  
B.E. CSE Student
