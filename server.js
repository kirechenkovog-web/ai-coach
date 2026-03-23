import express from "express";
import Anthropic from "@anthropic-ai/sdk";

const app = express();
app.use(express.json());
app.use(express.static("public"));

const client = new Anthropic({
  apiKey: process.env.ANTHROPIC_API_KEY,
});

const SYSTEM_PROMPT = `Ты — опытный ИИ-коуч по имени Алекс. Твоя задача — помогать людям достигать целей, разбираться в себе и двигаться вперёд.

Принципы работы:
- Задавай сильные открытые вопросы, чтобы помочь человеку найти ответы внутри себя
- Не давай готовых советов сразу — сначала помоги человеку прояснить ситуацию
- Будь тёплым, поддерживающим, но честным
- Используй технику GROW: Goal (цель), Reality (реальность), Options (варианты), Will (воля/план)
- Говори кратко и по делу, не лей воду
- Общайся на русском языке

Начни первое сообщение с тёплого приветствия и вопроса о том, над чем человек хочет поработать сегодня.`;

app.post("/api/chat", async (req, res) => {
  const { messages } = req.body;

  if (!messages || !Array.isArray(messages)) {
    return res.status(400).json({ error: "messages array required" });
  }

  try {
    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");

    const stream = client.messages.stream({
      model: "claude-opus-4-6",
      max_tokens: 1024,
      system: SYSTEM_PROMPT,
      messages,
    });

    for await (const event of stream) {
      if (
        event.type === "content_block_delta" &&
        event.delta.type === "text_delta"
      ) {
        res.write(`data: ${JSON.stringify({ text: event.delta.text })}\n\n`);
      }
    }

    res.write("data: [DONE]\n\n");
    res.end();
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: "Ошибка при обращении к Claude API" });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Коуч запущен: http://localhost:${PORT}`);
});
