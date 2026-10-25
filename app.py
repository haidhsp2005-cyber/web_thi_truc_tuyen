import gradio as gr
from app.main import app

# Mount FastAPI app vào Gradio Block để tương thích 100% với Hugging Face Spaces
demo = gr.mount_gradio_app(app, gr.Blocks(title="Hệ Thống Thi Trực Tuyến Long Cang 2026"), path="/gradio")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
