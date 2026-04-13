"""
app/ai/__init__.py — AI client abstractions (Vertex AI Gemini + Imagen).

Files to implement:
  vertex_client.py  — VertexAIClient wrapping google-cloud-aiplatform SDK,
                      with tenacity retry logic for transient API errors
  prompts.py        — All prompt templates in one place for easy iteration
"""
