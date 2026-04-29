import os
import sys
import time
import psutil
import atexit
import warnings
import traceback
import torch
import ffmpeg
import numpy as np
import customtkinter as ctk
from threading import Thread
from queue import Queue, Empty
from tkinter import filedialog, messagebox, Toplevel, Label
from PIL import Image

# Suppress Tcl/Tk and Buffer warnings
warnings.filterwarnings("ignore", category=UserWarning)

# --- GLOBAL PROCESS TRACKER ---
active_processes =[]

def cleanup_processes():
    for p in active_processes:
        try:
            parent = psutil.Process(p.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
        except: pass
    torch.cuda.empty_cache()

atexit.register(cleanup_processes)

# --- CACHE FOR KERNELS ---
_blur_kernels = {}

# --- ADVANCED CUDA KERNELS ---
def apply_gaussian_blur(x, kernel_size=15, sigma=5):
    channels = x.shape[1]
    key = (kernel_size, sigma, channels, x.device, x.dtype)
    if key not in _blur_kernels:
        coords = torch.arange(kernel_size, device=x.device) - (kernel_size - 1) / 2
        g = torch.exp(-(coords**2) / (2 * sigma**2))
        g = g / g.sum()
        kernel = (g.view(1, 1, kernel_size, 1) * g.view(1, 1, 1, kernel_size)).repeat(channels, 1, 1, 1).to(x.dtype)
        _blur_kernels[key] = kernel
    
    kernel = _blur_kernels[key]
    return torch.nn.functional.conv2d(x, kernel, padding=kernel_size // 2, groups=channels)

def haze_destruction_kernel(curr, stack_mean, stack_std, s):
    diff = torch.abs(curr - stack_mean)
    mirage_mask = torch.clamp((diff - (stack_std * s['haze_threshold'])) * s['haze_depth'], 0, 1)
    mirage_mask = apply_gaussian_blur(mirage_mask, kernel_size=7, sigma=2)
    return torch.lerp(curr, stack_mean, mirage_mask * s['haze_suppression'])

def advanced_mastering_kernel(img, s):
    detail_sig = s['detail_radius']
    k_size = int(6 * detail_sig + 1) | 1
    low_freq = apply_gaussian_blur(img, kernel_size=k_size, sigma=detail_sig)
    img = img + (img - low_freq) * s['detail_boost']
    img = (img - 128.0) * s['contrast'] + 128.0 + s['brightness']
    
    img = torch.pow(torch.clamp(img / 255.0, 1e-6, 1.0), s['gamma_inv']) * 255.0
    
    avg = img.mean(dim=1, keepdim=True)
    img = (img - avg) * s['vibrance'] + avg
    img = torch.lerp(avg, img, s['saturation'])
    
    sig = s['sharp_sigma']
    k_size = int(6 * sig + 1) | 1
    blurred = apply_gaussian_blur(img, kernel_size=k_size, sigma=sig)
    mask = img - blurred
    mask = torch.where(torch.abs(mask) > s['sharp_threshold'], mask, torch.zeros_like(mask))
    return (img + mask * s['sharp_amount']).clamp(0, 255)

# --- STUDIO INTERFACE ---
class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget; self.text = text; self.tip_window = None
        self.widget.bind("<Enter>", self.show_tip); self.widget.bind("<Leave>", self.hide_tip)
    def show_tip(self, event=None):
        if self.tip_window or not self.text: return
        x = self.widget.winfo_rootx() + 25; y = self.widget.winfo_rooty() + 25
        self.tip_window = tw = Toplevel(self.widget); tw.wm_overrideredirect(1); tw.wm_geometry("+%d+%d" % (x, y))
        Label(tw, text=self.text, justify='left', background="#ffffe0", relief='solid', borderwidth=1, font=("tahoma", "9", "normal")).pack(ipadx=1)
    def hide_tip(self, event=None):
        if self.tip_window: self.tip_window.destroy(); self.tip_window = None

class HeatWaveUltimateStudio(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("RTX 3090 MASTERING STUDIO v11.0 (Final Anti-Deadlock)")
        self.geometry("1550x1050")
        ctk.set_appearance_mode("dark")
        self.device = torch.device("cuda")
        self.input_path = ""
        self.stop_requested = False
        self.preview_cache =[]
        self.preview_idx = 0
        self.is_playing = False
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.setup_ui()

    def add_smart_slider(self, parent, label, v_min, v_max, v_def, row, col, desc, is_int=False):
        lbl = ctk.CTkLabel(parent, text=label, font=("Arial", 11, "bold")); lbl.grid(row=row, column=col, padx=10, pady=2, sticky="e")
        ToolTip(lbl, desc)
        s = ctk.CTkSlider(parent, from_=v_min, to=v_max, width=180)
        if is_int: s.configure(number_of_steps=int(v_max-v_min))
        s.set(v_def); s.grid(row=row, column=col+1, padx=10)
        v_lbl = ctk.CTkLabel(parent, text=str(v_def), width=45, text_color="#3498db"); v_lbl.grid(row=row, column=col+2, padx=5, sticky="w")
        s.configure(command=lambda v: v_lbl.configure(text=f"{int(v)}" if is_int else f"{float(v):.2f}"))
        return s

    def setup_ui(self):
        self.header = ctk.CTkLabel(self, text="ULTIMATE 4K MIRAGE SUPPRESSION", font=("Impact", 35), text_color="#3498db"); self.header.pack(pady=5)
        self.tabview = ctk.CTkTabview(self, width=1500, height=420); self.tabview.pack(padx=20, pady=5)
        
        t_haze = self.tabview.add("Mirage/Haze"); t_color = self.tabview.add("Color/Light"); t_det = self.tabview.add("Detail/Sharp"); t_perf = self.tabview.add("Performance")

        self.h_suppress = self.add_smart_slider(t_haze, "Haze Suppress", 0, 1, 0.85, 0, 0, "Strength of mirage removal.")
        self.h_depth = self.add_smart_slider(t_haze, "Haze Depth", 1, 50, 25, 1, 0, "Pixel vibration sensitivity.")
        self.h_thresh = self.add_smart_slider(t_haze, "Vibration Thresh", 0.1, 5.0, 1.2, 2, 0, "Distinguish haze from motion.")
        self.q_win = self.add_smart_slider(t_haze, "Stable Window", 5, 120, 45, 3, 0, "Frames for stability.", True)

        self.q_bright = self.add_smart_slider(t_color, "Brightness", -50, 50, 0, 0, 0, "Exposure offset.", True)
        self.q_cont = self.add_smart_slider(t_color, "Contrast", 0.5, 2.0, 1.1, 1, 0, "Luminance spread.")
        self.q_gamma = self.add_smart_slider(t_color, "Gamma", 0.2, 3.0, 1.0, 2, 0, "Midtone curve.")
        self.q_sat = self.add_smart_slider(t_color, "Saturation", 0, 3, 1.1, 0, 3, "Global intensity.")
        self.q_vib = self.add_smart_slider(t_color, "Vibrance", 1, 3, 1.3, 1, 3, "Smart color boost.")

        self.q_sharp = self.add_smart_slider(t_det, "Sharpen Amt", 0, 10, 1.5, 0, 0, "Edge sharpness.")
        self.q_radius = self.add_smart_slider(t_det, "Sharp Radius", 0.1, 5.0, 1.2, 1, 0, "Edge thickness.")
        self.q_detail_b = self.add_smart_slider(t_det, "Detail Boost", 0, 5, 0.8, 2, 0, "Texture enhancement.")
        self.q_detail_r = self.add_smart_slider(t_det, "Detail Radius", 0.1, 10, 2.5, 3, 0, "Texture size.")
        self.q_thresh = self.add_smart_slider(t_det, "Sharp Thresh", 0, 20, 2, 0, 3, "Noise ignore.", True)

        self.p_threads = self.add_smart_slider(t_perf, "Dec. Threads", 1, 32, 16, 0, 0, "CPU decoding threads.", True)
        self.p_preset = self.add_smart_slider(t_perf, "NVENC Preset", 1, 7, 2, 1, 0, "1=Best Quality, 7=Fastest.", True)
        self.q_bitrate = self.add_smart_slider(t_perf, "Bitrate (Mbps)", 10, 600, 150, 2, 0, "Target bandwidth.", True)

        self.preview_display = ctk.CTkLabel(self, text="PREVIEW AREA", height=400, fg_color="#0a0a0a", font=("Consolas", 18)); self.preview_display.pack(pady=5, padx=40, fill="both")
        self.progress = ctk.CTkProgressBar(self, width=1400); self.progress.set(0); self.progress.pack(pady=10)

        self.btn_f = ctk.CTkFrame(self); self.btn_f.pack(pady=10)
        ctk.CTkButton(self.btn_f, text="SELECT VIDEO", width=250, command=self.select_v).grid(row=0, column=0, padx=10)
        ctk.CTkButton(self.btn_f, text="GENERATE 30s PREVIEW", width=250, fg_color="#8e44ad", command=self.start_preview).grid(row=0, column=1, padx=10)
        ctk.CTkButton(self.btn_f, text="START EXPORT", width=250, fg_color="#27ae60", command=self.start_export).grid(row=0, column=2, padx=10)

    def select_v(self): self.input_path = filedialog.askopenfilename()
    def on_closing(self): self.stop_requested = True; self.is_playing = False; cleanup_processes(); self.destroy(); sys.exit(0)
    
    def start_preview(self): 
        if not self.input_path: return
        self.is_playing = False; self.preview_cache =[]
        self.preview_display.configure(image=None, text="GENERATING 30s PREVIEW...")
        Thread(target=self.run_pipeline, args=(True,), daemon=True).start()

    def start_export(self): 
        if self.input_path: 
            self.is_playing = False; self.preview_display.configure(image=None, text="INITIALIZING EXPORT...")
            Thread(target=self.run_pipeline, args=(False,), daemon=True).start()

    @staticmethod
    def _consume_stderr(pipe):
        """CRITICAL: Constantly empties the FFmpeg log buffers so the OS pipe never fills and deadlocks."""
        if pipe is None: return
        try:
            while True:
                data = pipe.read(8192)
                if not data: break
        except Exception: pass

    def _reader_thread(self, decoder, w, h, limit_frames, free_q, ready_q, pinned_tensors):
        frame_size = w * h * 3
        try:
            for _ in range(limit_frames):
                if self.stop_requested: break
                
                try: idx = free_q.get(timeout=1.0)
                except Empty: continue
                if idx is None: break
                
                view = memoryview(pinned_tensors[idx].numpy().reshape(-1))
                bytes_read = 0
                while bytes_read < frame_size:
                    n = decoder.stdout.readinto(view[bytes_read:])
                    if not n: break
                    bytes_read += n
                    
                if bytes_read < frame_size:
                    break 
                    
                ready_q.put(idx)
        except Exception as e:
            print(f"Reader thread error: {e}")
        finally:
            ready_q.put(None)

    def _writer_thread(self, encoder, free_q, ready_q, free_reader_q, out_pinned):
        try:
            while True:
                if self.stop_requested: break
                
                try: item = ready_q.get(timeout=1.0)
                except Empty: continue
                
                if item is None: break
                
                idx, event, reader_idx = item
                event.synchronize()  
                
                view = memoryview(out_pinned[idx].numpy().reshape(-1))
                try:
                    encoder.stdin.write(view)
                except Exception as e:
                    print(f"\n[ERROR] FFmpeg encoder pipe broken: {e}")
                    self.stop_requested = True
                    break
                
                free_q.put(idx)
                free_reader_q.put(reader_idx)
        except Exception as e:
            print(f"Writer thread error: {e}")
        finally:
            # CRITICAL: Safely close FFmpeg's video stream *from the exact thread that was writing to it*
            try:
                encoder.stdin.close()
            except Exception: pass

    def run_pipeline(self, is_preview=False):
        try:
            probe = ffmpeg.probe(self.input_path)
            v_info = next(s for s in probe['streams'] if s['codec_type'] == 'video')
            has_audio = any(s['codec_type'] == 'audio' for s in probe['streams'])
            
            orig_w, orig_h, fps_str = int(v_info['width']), int(v_info['height']), v_info['r_frame_rate']
            fps = eval(fps_str)
            nb_frames = int(v_info.get('nb_frames', 0))
            
            limit_frames = int(fps * 30) if is_preview else 999999999
            
            proc_w, proc_h = orig_w, orig_h
            if is_preview:
                if orig_h > 1080: target_h = 1080
                elif orig_h > 720: target_h = 720
                elif orig_h > 480: target_h = 480
                elif orig_h > 360: target_h = 360
                else: target_h = max(orig_h // 2, 144)
                
                target_w = int(orig_w * (target_h / orig_h))
                target_w -= target_w % 2
                target_h -= target_h % 2
                proc_w, proc_h = target_w, target_h

            dec_threads = int(self.p_threads.get())
            
            decoder_out_kwargs = {'format': 'rawvideo', 'pix_fmt': 'rgb24'}
            if is_preview:
                decoder_out_kwargs['s'] = f'{proc_w}x{proc_h}'
                
            decoder = ffmpeg.input(self.input_path, hwaccel='cuda', threads=dec_threads).output('pipe:', **decoder_out_kwargs).run_async(pipe_stdout=True, quiet=True)
            active_processes.append(decoder)
            
            # Start background thread to silently consume Decoder logs
            Thread(target=self._consume_stderr, args=(decoder.stderr,), daemon=True).start()

            if not is_preview:
                out_file = f"MASTER_{os.path.basename(self.input_path)}"
                
                out_kwargs = {
                    'vcodec': 'hevc_nvenc',
                    'preset': f'p{int(self.p_preset.get())}',
                    'video_bitrate': f'{int(self.q_bitrate.get())}M',
                    'pix_fmt': 'yuv420p10le',
                    'max_muxing_queue_size': 99999,
                    'shortest': None
                }

                vid_input = ffmpeg.input('pipe:', format='rawvideo', pix_fmt='rgb24', s=f'{orig_w}x{orig_h}', framerate=fps_str, thread_queue_size=10240)
                
                if has_audio:
                    orig_input = ffmpeg.input(self.input_path, thread_queue_size=10240)
                    encoder = ffmpeg.output(vid_input.video, orig_input.audio, out_file, acodec='copy', **out_kwargs)
                else:
                    encoder = ffmpeg.output(vid_input.video, out_file, **out_kwargs)
                    
                encoder = encoder.overwrite_output().run_async(pipe_stdin=True, quiet=True)
                active_processes.append(encoder)
                
                # Start background thread to silently consume Encoder logs preventing freeze
                Thread(target=self._consume_stderr, args=(encoder.stderr,), daemon=True).start()

            s_params = {
                'contrast': float(self.q_cont.get()), 'brightness': float(self.q_bright.get()),
                'gamma_inv': 1.0 / float(self.q_gamma.get()), 'vibrance': float(self.q_vib.get()),
                'saturation': float(self.q_sat.get()), 'sharp_amount': float(self.q_sharp.get()),
                'sharp_sigma': float(self.q_radius.get()), 'sharp_threshold': float(self.q_thresh.get()),
                'detail_boost': float(self.q_detail_b.get()), 'detail_radius': float(self.q_detail_r.get()),
                'haze_suppression': float(self.h_suppress.get()), 'haze_depth': float(self.h_depth.get()),
                'haze_threshold': float(self.h_thresh.get())
            }

            win_size = int(self.q_win.get())
            num_buffers = 8
            
            pinned_tensors =[torch.empty((proc_h, proc_w, 3), dtype=torch.uint8, pin_memory=True) for _ in range(num_buffers)]
            out_pinned =[torch.empty((orig_h, orig_w, 3), dtype=torch.uint8, pin_memory=True) for _ in range(num_buffers)] if not is_preview else[]
            
            free_reader_queue, ready_reader_queue = Queue(), Queue()
            free_writer_queue, ready_writer_queue = Queue(), Queue()
            
            for i in range(num_buffers):
                free_reader_queue.put(i)
                if not is_preview: free_writer_queue.put(i)

            reader_t = Thread(target=self._reader_thread, args=(decoder, proc_w, proc_h, limit_frames, free_reader_queue, ready_reader_queue, pinned_tensors), daemon=True)
            reader_t.start()
            
            if not is_preview:
                writer_t = Thread(target=self._writer_thread, args=(encoder, free_writer_queue, ready_writer_queue, free_reader_queue, out_pinned), daemon=True)
                writer_t.start()

            buffer_tensor = torch.empty((win_size, 3, proc_h, proc_w), dtype=torch.float16, device=self.device)
            running_sum = torch.zeros((1, 3, proc_h, proc_w), dtype=torch.float32, device=self.device)
            running_sq_sum = torch.zeros((1, 3, proc_h, proc_w), dtype=torch.float32, device=self.device)

            stream = torch.cuda.Stream()
            frame_count = 0
            start_time = time.time()
            
            with torch.cuda.stream(stream), torch.inference_mode(), torch.amp.autocast('cuda'):
                while True:
                    if self.stop_requested: break
                    
                    idx = None
                    while not self.stop_requested:
                        try:
                            idx = ready_reader_queue.get(timeout=1.0)
                            break
                        except Empty: pass
                    
                    if self.stop_requested or idx is None: break 
                        
                    curr = pinned_tensors[idx].to(self.device, non_blocking=True).permute(2,0,1).half().unsqueeze(0)
                    curr_f32 = curr.to(torch.float32)
                    curr_sq = torch.square(curr_f32)
                    buf_idx = frame_count % win_size

                    if frame_count < win_size:
                        buffer_tensor[buf_idx].copy_(curr.squeeze(0))
                        running_sum.add_(curr_f32)
                        running_sq_sum.add_(curr_sq)
                        num_elements = frame_count + 1
                    else:
                        old = buffer_tensor[buf_idx].unsqueeze(0).to(torch.float32)
                        old_sq = torch.square(old)
                        running_sum.add_(curr_f32).sub_(old)
                        running_sq_sum.add_(curr_sq).sub_(old_sq)
                        buffer_tensor[buf_idx].copy_(curr.squeeze(0))
                        num_elements = win_size

                    mean = (running_sum / num_elements).to(torch.float16)
                    std = torch.sqrt(torch.clamp((running_sq_sum / num_elements) - torch.square(running_sum / num_elements), 0, 65500) + 1e-5).to(torch.float16)
                    
                    restored = haze_destruction_kernel(curr, mean, std, s_params)
                    final = advanced_mastering_kernel(restored, s_params)
                    
                    if is_preview:
                        scale = 380 / proc_h
                        sbs = torch.cat((torch.nn.functional.interpolate(curr, scale_factor=scale, mode='bilinear'), torch.nn.functional.interpolate(final, scale_factor=scale, mode='bilinear')), dim=3).squeeze(0)
                        
                        stream.synchronize()
                        free_reader_queue.put(idx) 
                        self.preview_cache.append(Image.fromarray(sbs.clamp(0, 255).byte().permute(1,2,0).cpu().numpy()))
                        
                        if frame_count % 10 == 0: 
                            pct = min(frame_count / limit_frames, 1.0)
                            self.after(0, lambda p=pct: self.progress.set(p))
                    else:
                        final_out = final.squeeze(0).clamp(0, 255).byte().permute(1, 2, 0)
                        
                        out_idx = None
                        while not self.stop_requested:
                            try:
                                out_idx = free_writer_queue.get(timeout=1.0)
                                break
                            except Empty: pass
                            
                        if self.stop_requested or out_idx is None: break
                        
                        out_pinned[out_idx].copy_(final_out, non_blocking=True)
                        event = torch.cuda.Event()
                        event.record(stream)
                        ready_writer_queue.put((out_idx, event, idx))
                        
                        if frame_count % 10 == 0: 
                            elapsed = time.time() - start_time
                            proc_fps = frame_count / elapsed if elapsed > 0 else 0
                            
                            eta_str = "Calculating..."
                            if nb_frames > 0 and frame_count > 0:
                                eta_sec = (elapsed / frame_count) * (nb_frames - frame_count)
                                eta_str = f"{int(eta_sec // 60)}m {int(eta_sec % 60)}s"
                                pct = min(frame_count / nb_frames, 1.0)
                                self.after(0, lambda p=pct: self.progress.set(p))
                            
                            stats_text = (
                                f"EXPERT PROCESSING DASHBOARD\n"
                                f"───────────────────────────────────────\n\n"
                                f"Resolution:         {orig_w}x{orig_h}\n"
                                f"Target Frame Rate:  {fps_str} fps\n"
                                f"Target Bitrate:     {int(self.q_bitrate.get())} Mbps\n"
                                f"Hardware Encoder:   HEVC (NVENC) Preset P{int(self.p_preset.get())}\n"
                                f"Audio Track:        {'Multiplexed (Copied)' if has_audio else 'None (Silent)'}\n\n"
                                f"Frames Processed:   {frame_count} / {nb_frames if nb_frames > 0 else 'Unknown'}\n"
                                f"Current Speed:      {proc_fps:.2f} fps\n"
                                f"Elapsed Time:       {int(elapsed // 60)}m {int(elapsed % 60)}s\n"
                                f"Estimated Wait:     {eta_str}"
                            )
                            self.after(0, lambda t=stats_text: self.preview_display.configure(text=t))

                    frame_count += 1

            # == SAFE FINALIZATION & MUXING SEQUENCE ==
            try:
                decoder.stdout.close()
                decoder.terminate()
            except Exception: pass

            if not is_preview:
                self.after(0, lambda: self.preview_display.configure(text="FINALIZING EXPORT...\n(Flushing buffers and Muxing Audio)\n\nThis may take a minute. Please wait..."))
                
                # Command writer thread to wrap up and gracefully close the video pipe itself
                ready_writer_queue.put(None)
                writer_t.join(timeout=60.0) 
                
                # Main thread waits for FFmpeg to finish encoding the end of the audio track
                try:
                    encoder.wait(timeout=180) # 3 Min max wait for Audio Multiplexing completion
                except Exception as e:
                    print(f"Forcing FFmpeg exit: {e}")
                    encoder.terminate()
                
                output_info = (
                    f"Video Master Created Successfully!\n\n"
                    f"Output Resolution: {orig_w}x{orig_h}\n"
                    f"Frame Rate: {fps_str} fps\n"
                    f"Target Bitrate: {int(self.q_bitrate.get())} Mbps\n"
                    f"Hardware Preset: P{int(self.p_preset.get())}\n"
                    f"Total Frames Processed: {frame_count}"
                )
                
                print(f"\n--- EXPORT COMPLETED ---\n{output_info}\n------------------------")
                self.after(0, lambda: self.progress.set(1.0))
                self.after(0, lambda: self.preview_display.configure(text="EXPORT FINISHED SUCCESSFULLY"))
                self.after(0, lambda: messagebox.showinfo("Export Details", output_info))
            else: 
                stream.synchronize()
                self.after(0, lambda: self.progress.set(1.0))
                self.after(50, self.init_preview_playback)

        except Exception as e: 
            print(f"Pipeline Error: {e}")
            traceback.print_exc()
        finally: 
            cleanup_processes()

    def init_preview_playback(self):
        if self.stop_requested: return
        self.is_playing = True; self.preview_idx = 0; self.play_prev()

    def play_prev(self):
        if self.is_playing and self.preview_cache and not self.stop_requested:
            try:
                pil_img = self.preview_cache[self.preview_idx]
                ctk_img = ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(pil_img.width, pil_img.height))
                self.preview_display.configure(image=ctk_img, text="")
                self.preview_display._ctk_image_ref = ctk_img 
                self.preview_idx = (self.preview_idx + 1) % len(self.preview_cache)
                self.after(33, self.play_prev)
            except Exception: pass

if __name__ == "__main__":
    app = HeatWaveUltimateStudio()
    app.mainloop()