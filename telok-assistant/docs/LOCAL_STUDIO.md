# Local studio

Uses an existing ComfyUI portable installation and SDXL 1.0 checkpoint. Models are not copied into Telok.
Set TELOK_COMFY_ROOT or the ignored data/local-studio-path.txt to your portable installation directory.
Start with scripts/start.ps1 -Bot -LocalStudio. ComfyUI listens on loopback port 8188 only; custom nodes are disabled.

Open a completed production task in /assistant. Upload a photo/keyframe, generate locally, vary the seed,
review the image and approve the exact frame version. Generated frames use text-to-image or img2img
with the first supplied photo. Product identity is not guaranteed; manual review is required.

Non-English scene descriptions are compiled once per pack through the configured text route. This consumes
that route's quota. Image generation and static storyboard rendering run locally without video-provider calls.
The storyboard is a separate watermarked static MP4, not a generated-motion clip.

Telegram: reply /frames to a completed production result to create frames and a storyboard. Reply /storyboard
to render existing frames. An explicit task ID may be supplied after either command.

An approved current frame is sent to Seedance as first_frame with ratio adaptive. It is never mixed with
reference_image mode. Paid generation still requires separate credentials, budgets and explicit action.
No local generative-motion video model is currently integrated.
