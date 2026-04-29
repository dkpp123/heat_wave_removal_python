# HeatWave Video Mastering Studio

HeatWave Ultimate Studio is a GPU-accelerated video mastering and restoration tool built in Python. It utilizes PyTorch and FFmpeg to perform real-time mirage/haze suppression, advanced color grading, detail enhancement, and sharp rendering. 

Designed for raw speed, it uses pinned-memory buffers, multi-threading, and zero-copy transfers to push your NVIDIA GPU to its limits, offering both a quick 30-second preview mode and full-length HEVC (NVENC) high-speed exporting.

![UI Preview](https://via.placeholder.com/800x450.png?text=HeatWave+Mastering+Studio+UI) *(You can replace this link with a real screenshot of your app)*

---

## 🖥️ System Requirements

Before you begin, ensure your system meets these requirements:
* **Operating System:** Windows 10 / 11 (Linux is supported but these instructions focus on Windows).
* **GPU:** An **NVIDIA GPU** is **strictly required**. The script utilizes NVIDIA's `CUDA` cores for math processing and `NVENC` hardware for fast video encoding.
* **CPU:** Any modern AMD or Intel CPU.

---

## ⚙️ Step 1: Prerequisite Software Installations

If you are starting from scratch, you need to install Python and FFmpeg on your computer.

### 1. Install Python
1. Download Python from the [official website](https://www.python.org/downloads/). (Recommended version: Python 3.10 or 3.11).
2. Run the installer.
3. ⚠️ **CRITICAL STEP:** At the very bottom of the first installer screen, **check the box that says "Add python.exe to PATH"** before clicking Install.

### 2. Install FFmpeg
The script uses FFmpeg behind the scenes to decode and encode video. 
1. Download the FFmpeg Windows build from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) (Download the `ffmpeg-release-essentials.zip`).
2. Extract the downloaded `.zip` folder.
3. Rename the extracted folder to `ffmpeg` and move it to the root of your `C:` drive (so it sits at `C:\ffmpeg`).
4. **Add FFmpeg to your System PATH:**
   * Click your Windows Start Menu, type `Environment Variables`, and hit Enter.
   * Click the **Environment Variables...** button.
   * Under "System variables" (the bottom list), scroll down, select `Path`, and click **Edit...**
   * Click **New**, type `C:\ffmpeg\bin`, and press Enter.
   * Click OK on all three windows to save and close.

---

## 📦 Step 2: Install Python Dependencies

Now we need to install the libraries that make the code work. Open your computer's **Command Prompt** (Press the Windows Key, type `cmd`, and press Enter).

### 1. Install PyTorch with CUDA (NVIDIA GPU Support)
Because this app relies heavily on the GPU, you must install the specific version of PyTorch that talks to NVIDIA graphics cards. Run this command:
```bash
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
(Note: This is a large download and may take a few minutes).
2. Install the Required Python Packages
Next, install the rest of the libraries required for the UI and video processing. Run this command:
code
Bash
pip install ffmpeg-python numpy customtkinter psutil Pillow
🚀 Step 3: Running the Application
Once everything is installed, you are ready to use the app!
Download the videofix.py script from this repository.
Place it in a folder of your choice.
Open a Command Prompt in that folder (You can do this by clicking the address bar in File Explorer, typing cmd, and hitting Enter).
Run the script using Python:
code
Bash
python videofix.py
The graphical interface will pop up.
How to Use:
Click SELECT VIDEO and choose an .mp4, .mkv, etc.
Adjust the Mirage/Haze, Color, and Detail sliders to your liking.
Click GENERATE 30s PREVIEW to see a fast, downscaled split-screen preview of your changes in real-time.
Click START EXPORT to process the entire video at its original resolution. The final video will be saved in the same folder as the script, prefixed with MASTER_.
🛠️ Troubleshooting
Error: FileNotFoundError: [WinError 2] The system cannot find the file specified
Cause: Python cannot find FFmpeg.
Fix: Ensure you completed all the steps in the "Install FFmpeg" section and specifically added C:\ffmpeg\bin to your System Path. Restart your computer if you just added it.
Error: AssertionError: Torch not compiled with CUDA enabled
Cause: You installed the standard CPU version of PyTorch instead of the GPU version.
Fix: Run pip uninstall torch and then re-run the PyTorch installation command listed in Step 2.1. Update your NVIDIA graphics drivers.
App Crashes immediately upon clicking "Start Export"
Cause: NVENC (NVIDIA Encoder) failed to initialize.
Fix: Ensure your NVIDIA GPU supports HEVC (H.265) encoding (GTX 10-series or newer). Try lowering the target bitrate.
