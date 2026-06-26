import os
import json
import tkinter as tk
from tkinter import filedialog
from tkinter import messagebox
from tkinter import ttk
import subprocess
import platform

def sanitize_filename(filename):
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, '_')
    return filename

def select_folder(export_mode):
    folder_path = filedialog.askdirectory()
    if folder_path:
        output_folder = os.path.join(folder_path, '导出')
        os.makedirs(output_folder, exist_ok=True)
        if export_mode == 'video':
            process_video_folder(folder_path, output_folder)
        else:
            process_audio_folder(folder_path, output_folder)

def get_video_title(entry_json_path):
    """从entry.json中获取视频标题，优先使用part字段（合集分集），否则使用title字段"""
    try:
        with open(entry_json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 尝试从page_data中获取part字段
        page_data = data.get('page_data', {})
        if isinstance(page_data, dict):
            part_title = page_data.get('part', '')
            if part_title and part_title.strip():
                return part_title.strip()
        
        # 尝试根级别的part字段
        part_title = data.get('part', '')
        if part_title and part_title.strip():
            return part_title.strip()
        
        # 使用title字段
        title = data.get('title', '')
        if title and title.strip():
            return title.strip()
        
        return None
    except (json.JSONDecodeError, KeyError, IOError) as e:
        print(f"读取JSON失败 {entry_json_path}: {e}")
        return None

def process_video_folder(path, output_folder):
    video_types = ['80', '64', '32', '16']
    
    # 用于跟踪已处理的视频标题和计数
    title_counts = {}
    processed_count = 0
    skipped_count = 0
    
    # 收集所有视频文件夹
    video_folders = []
    for root, dirs, files in os.walk(path):
        for dir_name in dirs:
            if dir_name.startswith('c_'):
                video_folder = os.path.join(root, dir_name)
                if os.path.isdir(video_folder):
                    video_folders.append(video_folder)
    
    print(f"找到 {len(video_folders)} 个视频文件夹")
    
    for video_folder in video_folders:
        entry_json_path = os.path.join(video_folder, 'entry.json')
        if not os.path.exists(entry_json_path):
            print(f"未找到entry.json: {video_folder}")
            skipped_count += 1
            continue
        
        title = get_video_title(entry_json_path)
        if not title:
            print(f"无法获取视频标题: {video_folder}")
            skipped_count += 1
            continue
        
        safe_title = sanitize_filename(title)
        
        # 检查这是否是合集视频（通过检查是否有其他相同根目录的视频）
        is_collection = False
        base_path = os.path.dirname(video_folder)
        for other_folder in video_folders:
            if other_folder != video_folder and os.path.dirname(other_folder) == base_path:
                is_collection = True
                break
        
        # 确定文件名
        if title in title_counts:
            # 如果是合集视频且有重复标题，添加序号
            if is_collection:
                count = title_counts[title]
                output_filename = f"{safe_title}_{count}.mp4"
                title_counts[title] += 1
            else:
                # 单个视频不应该重复，所以这应该是合集视频
                output_filename = f"{safe_title}.mp4"
        else:
            # 首次出现的标题
            output_filename = f"{safe_title}.mp4"
            title_counts[title] = 1
        
        output_filepath = os.path.join(output_folder, output_filename)
        
        # 确保文件不会覆盖已存在的文件
        counter = 1
        while os.path.exists(output_filepath):
            base_name, ext = os.path.splitext(output_filename)
            output_filename = f"{base_name}_{counter}{ext}"
            output_filepath = os.path.join(output_folder, output_filename)
            counter += 1
            if counter > 100:
                break
        
        video_file = None
        audio_file = None
        found_vt = None
        for vt in video_types:
            video_path = os.path.join(video_folder, vt, 'video.m4s')
            audio_path = os.path.join(video_folder, vt, 'audio.m4s')
            if os.path.exists(video_path) and os.path.exists(audio_path):
                video_file = video_path
                audio_file = audio_path
                found_vt = vt
                break
        
        if not video_file or not audio_file:
            print(f"未找到视频或音频文件: {video_folder}")
            skipped_count += 1
            continue
        
        try:
            print(f"处理视频: {title}")
            
            shell_flag = platform.system() == 'Windows'
            result = subprocess.run([
                "ffmpeg",
                "-i", video_file,
                "-i", audio_file,
                "-c", "copy",
                "-y",
                output_filepath
            ], check=True, shell=shell_flag, 
               stdout=subprocess.PIPE, 
               stderr=subprocess.PIPE,
               universal_newlines=True,
               encoding='utf-8',
               errors='replace')
            
            if os.path.exists(output_filepath):
                file_size = os.path.getsize(output_filepath) / (1024*1024)  # MB
                if file_size > 0.1:
                    print(f"成功导出: {output_filename} ({file_size:.1f}MB)")
                    processed_count += 1
                else:
                    print(f"文件太小可能有问题: {output_filename} ({file_size:.1f}MB)")
                    os.remove(output_filepath)
                    skipped_count += 1
            else:
                print(f"文件创建失败: {output_filename}")
                skipped_count += 1
                
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr[:500] if e.stderr else str(e)
            print(f"合并出错 {title}: {error_msg}")
            skipped_count += 1
        except Exception as e:
            print(f"错误 {title}: {str(e)[:200]}")
            skipped_count += 1
    
    print(f"\n处理完成！成功导出: {processed_count}个，跳过: {skipped_count}个")

def process_audio_folder(path, output_folder):
    video_types = ['80', '64', '32', '16']
    
    title_counts = {}
    processed_count = 0
    skipped_count = 0
    
    audio_folders = []
    for root, dirs, files in os.walk(path):
        for dir_name in dirs:
            if dir_name.startswith('c_'):
                audio_folder = os.path.join(root, dir_name)
                if os.path.isdir(audio_folder):
                    audio_folders.append(audio_folder)
    
    print(f"找到 {len(audio_folders)} 个音频文件夹")
    
    for audio_folder in audio_folders:
        entry_json_path = os.path.join(audio_folder, 'entry.json')
        if not os.path.exists(entry_json_path):
            print(f"未找到entry.json: {audio_folder}")
            skipped_count += 1
            continue
        
        title = get_video_title(entry_json_path)
        if not title:
            print(f"无法获取音频标题: {audio_folder}")
            skipped_count += 1
            continue
        
        safe_title = sanitize_filename(title)
        
        is_collection = False
        base_path = os.path.dirname(audio_folder)
        for other_folder in audio_folders:
            if other_folder != audio_folder and os.path.dirname(other_folder) == base_path:
                is_collection = True
                break
        
        if title in title_counts:
            if is_collection:
                count = title_counts[title]
                output_filename = f"{safe_title}_{count}.aac"
                title_counts[title] += 1
            else:
                output_filename = f"{safe_title}.aac"
        else:
            output_filename = f"{safe_title}.aac"
            title_counts[title] = 1
        
        output_filepath = os.path.join(output_folder, output_filename)
        
        counter = 1
        while os.path.exists(output_filepath):
            base_name, ext = os.path.splitext(output_filename)
            output_filename = f"{base_name}_{counter}{ext}"
            output_filepath = os.path.join(output_folder, output_filename)
            counter += 1
            if counter > 100:
                break
        
        audio_file = None
        found_vt = None
        for vt in video_types:
            audio_path = os.path.join(audio_folder, vt, 'audio.m4s')
            if os.path.exists(audio_path):
                audio_file = audio_path
                found_vt = vt
                break
        
        if not audio_file:
            print(f"未找到音频文件: {audio_folder}")
            skipped_count += 1
            continue
        
        try:
            print(f"处理音频: {title}")
            
            shell_flag = platform.system() == 'Windows'
            result = subprocess.run([
                "ffmpeg",
                "-i", audio_file,
                "-c:a", "copy",
                "-f", "adts",
                "-y",
                output_filepath
            ], check=True, shell=shell_flag,
               stdout=subprocess.PIPE,
               stderr=subprocess.PIPE,
               universal_newlines=True,
               encoding='utf-8',
               errors='replace')
            
            if os.path.exists(output_filepath):
                file_size = os.path.getsize(output_filepath) / (1024*1024)
                if file_size > 0.1:
                    print(f"成功导出音频: {output_filename} ({file_size:.1f}MB)")
                    processed_count += 1
                else:
                    print(f"音频文件太小可能有问题: {output_filename} ({file_size:.1f}MB)")
                    os.remove(output_filepath)
                    skipped_count += 1
            else:
                print(f"音频创建失败: {output_filename}")
                skipped_count += 1
                
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr[:500] if e.stderr else str(e)
            print(f"音频导出错误 {title}: {error_msg}")
            skipped_count += 1
        except Exception as e:
            print(f"错误 {title}: {str(e)[:200]}")
            skipped_count += 1
    
    print(f"\n音频处理完成！成功导出: {processed_count}个，跳过: {skipped_count}个")

def main():
    root = tk.Tk()
    root.geometry("300x180")
    root.title("b站离线缓存导出工具")
    export_type = tk.StringVar(value='video')

    lbl1 = tk.Label(root, text="当前版本可以自动扫描大多数缓存")
    lbl1.pack(pady=3)

    lbl2 = tk.Label(root, text="Ciallo～ (∠・ω< )⌒★")
    lbl2.pack(pady=3)

    radio_frame = tk.Frame(root)
    radio_frame.pack(pady=5)
    tk.Radiobutton(radio_frame, text="导出视频", variable=export_type, value='video').pack(side=tk.LEFT, padx=5)
    tk.Radiobutton(radio_frame, text="导出音频", variable=export_type, value='audio').pack(side=tk.LEFT, padx=5)

    btn = tk.Button(root, text="请选择你的英雄", 
                  command=lambda: select_folder(export_type.get()))
    btn.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)

    root.mainloop()

if __name__ == "__main__":
    main()