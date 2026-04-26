import os
import subprocess
from tkinter import filedialog, Tk, Frame, Button, Listbox, Label, Entry, IntVar, StringVar, Checkbutton, END, EXTENDED
from tkinter import ttk
from tkinter import messagebox
import biliout2
from datetime import timedelta
import shutil
from openai import OpenAI
import time
import threading

files_to_convert = []
if_llm = True
is_converting = False  # 转换状态标志


def get_unique_filename(output_path):
    #检查输出文件是否存在，如果存在则添加数字后缀
    if not os.path.exists(output_path):
        return output_path
    
    base, ext = os.path.splitext(output_path)
    counter = 1
    
    while True:
        new_path = f"{base}_{counter}{ext}"
        if not os.path.exists(new_path):
            return new_path
        counter += 1


def extract_metadata(input_file):
    """使用 ffprobe 提取源文件的元数据，返回字典"""
    try:
        ffprobe_cmd = (
            f'ffprobe -v quiet -print_format json '
            f'-show_format -show_streams "{input_file}"'
        )
        result = subprocess.run(ffprobe_cmd, shell=True, capture_output=True, text=True)
        import json
        info = json.loads(result.stdout)
        tags = info.get("format", {}).get("tags", {})
        # 规范化 key 为小写
        return {k.lower(): v for k, v in tags.items()}
    except Exception as e:
        print(f"提取元数据失败: {e}")
        return {}


def build_metadata_args(tags):
    """将元数据字典转换为 ffmpeg -metadata 参数字符串"""
    if not tags:
        return ""
    args = ""
    # 常见字段白名单，避免写入不兼容字段
    allowed_keys = {
        "title", "artist", "album", "album_artist", "date", "year",
        "track", "genre", "comment", "composer", "lyrics", "description",
        "copyright", "encoder", "encoded_by"
    }
    for k, v in tags.items():
        if k in allowed_keys:
            # 转义双引号
            v_escaped = v.replace('"', '\\"')
            args += f' -metadata {k}="{v_escaped}"'
    return args


def has_cover_art(input_file):
    """检查源文件是否含有封面图片流"""
    try:
        ffprobe_cmd = (
            f'ffprobe -v quiet -print_format json '
            f'-show_streams "{input_file}"'
        )
        result = subprocess.run(ffprobe_cmd, shell=True, capture_output=True, text=True)
        import json
        info = json.loads(result.stdout)
        for stream in info.get("streams", []):
            codec_type = stream.get("codec_type", "")
            disposition = stream.get("disposition", {})
            # 封面通常是 video 流且带有 attached_pic disposition
            if codec_type == "video" and disposition.get("attached_pic", 0) == 1:
                return True
        return False
    except Exception as e:
        print(f"检查封面失败: {e}")
        return False


def update_progress(message):
    #在GUI中更新进度信息
    def _update():
        progress_label.config(text=message)
    root.after(0, _update)


def update_file_list_display():
    #更新文件列表显示
    def _update():
        file_list.delete(0, END)
        for file in files_to_convert:
            file_list.insert(END, f"待转换文件: {file}")
    root.after(0, _update)


def set_converting_state(state):
    global is_converting
    is_converting = state
    def _update():
        if state:
            convert_button.config(state='disabled')
            add_files_button.config(state='disabled')
            remove_files_button.config(state='disabled')
        else:
            convert_button.config(state='normal')
            add_files_button.config(state='normal')
            remove_files_button.config(state='normal')
    root.after(0, _update)


def check_and_split_video(selected_file, output_resolution, video_bitrate, audio_bitrate, audio_block_size, framerate, split_video_time, if_llm_flag):
    ffprobe_cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{selected_file}"'
    result = subprocess.run(ffprobe_cmd, shell=True, capture_output=True, text=True)
    duration_seconds = float(result.stdout)

    if duration_seconds <= split_video_time:
        return convert_to_amv(selected_file, output_resolution, video_bitrate, audio_bitrate, audio_block_size, framerate, if_llm_flag)

    num_segments = int(duration_seconds) // split_video_time + (1 if duration_seconds % split_video_time > 0 else 0)
    split_files = []
    amv_files = []

    if if_use_llm.get() == 1:
        local_if_llm = False
        base_name = llm_rename(os.path.splitext(os.path.basename(selected_file))[0])
    else:
        local_if_llm = True
        base_name = os.path.splitext(os.path.basename(selected_file))[0]

    for i in range(num_segments):
        start_time = timedelta(seconds=i * split_video_time)
        end_time = timedelta(seconds=(i + 1) * split_video_time)
        
        output_file = f"{base_name}_{i + 1}{os.path.splitext(selected_file)[1]}"
        output_file = get_unique_filename(output_file)
        split_files.append(output_file)
        
        ffmpeg_cmd = f'ffmpeg -i "{selected_file}" -ss {start_time} -to {end_time} -c copy "{output_file}"'
        subprocess.run(ffmpeg_cmd, shell=True)
        
        amv_path = convert_to_amv(output_file, output_resolution, video_bitrate, audio_bitrate, audio_block_size, framerate, local_if_llm)
        amv_files.append(amv_path)

    for file in split_files:
        if os.path.exists(file):
            os.remove(file)

    return amv_files


def convert_to_amv(input_file, output_resolution, video_bitrate, audio_bitrate, audio_block_size, framerate, if_llm_local):
    if if_use_llm.get() == 1 and if_llm_local:
        base_name = os.path.splitext(os.path.basename(input_file))[0]
        output_filename = llm_rename(base_name) + ".amv"
        print("成功调用llm:", output_filename)
    else:
        if if_spilit.get() == 1:
            base_name = os.path.splitext(os.path.basename(input_file))[0]
            output_filename = base_name + ".amv"
        else:
            output_filename = os.path.splitext(input_file)[0] + ".amv"
    
    output_filename = get_unique_filename(output_filename)
    
    if if_rotate.get() == 1:
        try:
            ffmpeg_cmd_amv = f'ffmpeg -i "{input_file}" -vf "transpose=2" -ac 1 -ar 22050 -acodec adpcm_ima_amv -block_size {audio_block_size} -vcodec amv -pix_fmt yuvj420p -strict -1 -s {output_resolution[0]}x{output_resolution[1]} -b:v {video_bitrate}k -b:a {audio_bitrate}k -r {framerate} "{output_filename}"'
            subprocess.run(ffmpeg_cmd_amv, shell=True, check=True)
        except subprocess.CalledProcessError as e:
            print(f"转换错误: {e}")
    else:
        if video_bitrate == 0:
            try:
                ffmpeg_cmd_amv = f'ffmpeg -i "{input_file}" -ac 1 -ar 22050 -acodec adpcm_ima_amv -block_size {audio_block_size} -vcodec amv -pix_fmt yuvj420p -strict -1 -s {output_resolution[0]}x{output_resolution[1]} -b:a {audio_bitrate}k -r {framerate} "{output_filename}"'
                subprocess.run(ffmpeg_cmd_amv, shell=True, check=True)
            except subprocess.CalledProcessError as e:
                print(f"转换错误: {e}")
        else:
            try:
                ffmpeg_cmd_amv = f'ffmpeg -i "{input_file}" -ac 1 -ar 22050 -acodec adpcm_ima_amv -block_size {audio_block_size} -vcodec amv -pix_fmt yuvj420p -strict -1 -s {output_resolution[0]}x{output_resolution[1]} -b:v {video_bitrate}k -b:a {audio_bitrate}k -r {framerate} "{output_filename}"'
                subprocess.run(ffmpeg_cmd_amv, shell=True, check=True)
            except subprocess.CalledProcessError as e:
                print(f"转换错误: {e}")
    
    return output_filename


def convert_to_mp3(input_file, audio_quality):
    if if_use_llm.get() == 1:
        base_name = os.path.splitext(os.path.basename(input_file))[0]
        output_filename = llm_rename(base_name) + ".mp3"
    else:
        output_filename = os.path.splitext(input_file)[0] + ".mp3"
    
    output_filename = get_unique_filename(output_filename)

    # 提取元数据与封面
    tags = extract_metadata(input_file)
    metadata_args = build_metadata_args(tags)
    cover_present = has_cover_art(input_file)

    try:
        if cover_present:
            # 保留封面：使用 -i 二次输入封面流，map_metadata 继承文本元数据
            ffmpeg_cmd_mp3 = (
                f'ffmpeg -i "{input_file}" -i "{input_file}" '
                f'-map 0:a:0 -map 1:v:0 '
                f'-c:a libmp3lame -q:a {audio_quality} '
                f'-c:v copy -id3v2_version 3 '
                f'-metadata:s:v title="Album cover" '
                f'-metadata:s:v comment="Cover (front)" '
                f'{metadata_args} '
                f'"{output_filename}"'
            )
        else:
            ffmpeg_cmd_mp3 = (
                f'ffmpeg -i "{input_file}" -vn '
                f'-c:a libmp3lame -q:a {audio_quality} '
                f'-id3v2_version 3 '
                f'{metadata_args} '
                f'"{output_filename}"'
            )
        subprocess.run(ffmpeg_cmd_mp3, shell=True, check=True)
        print(f"MP3转换完成（含元数据）: {output_filename}")
    except subprocess.CalledProcessError as e:
        print(f"转换错误: {e}")


def convert_to_avi(input_file, output_resolution, video_bitrate, audio_bitrate, audio_block_size, framerate):
    if if_use_llm.get() == 1:
        base_name = os.path.splitext(os.path.basename(input_file))[0]
        output_filename = llm_rename(base_name) + ".avi"
    else:
        output_filename = os.path.splitext(input_file)[0] + ".avi"
    
    output_filename = get_unique_filename(output_filename)
    
    #基础命令
    ffmpeg_cmd_avi = f'ffmpeg -i "{input_file}" -s {output_resolution[0]}x{output_resolution[1]}'
    #构建命令
    if video_bitrate > 0:
        ffmpeg_cmd_avi += f' -b:v {video_bitrate}k'
    if framerate > 0:
        ffmpeg_cmd_avi += f' -r {framerate}'
    ffmpeg_cmd_avi += f' -vcodec libxvid -qmin 1 -qmax 20 -g 24 -me_method epzs -mbd 1'
    ffmpeg_cmd_avi += f' -acodec aac -ac 2'
    if audio_bitrate > 0:
        ffmpeg_cmd_avi += f' -b:a {audio_bitrate}k'
    ffmpeg_cmd_avi += f' "{output_filename}"'
    
    try:
        print(f"DEBUG: {ffmpeg_cmd_avi}")
        subprocess.run(ffmpeg_cmd_avi, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"转换错误: {e}")
    
    return output_filename


def select_files():
    input_files = filedialog.askopenfilenames(
        title="选择要转换的文件", 
        filetypes=(("所有文件", "*.*"), ("文件", "*.mp4;*.mp3;*.wav;*.aac;*.amv;*.m4s;*.avi"))
    )
    return input_files


def add_files_to_list():
    global files_to_convert
    if is_converting:
        messagebox.showwarning("警告", "正在转换中，无法添加文件")
        return
    selected_files = select_files()
    for file in selected_files:
        if file not in files_to_convert:
            files_to_convert.append(file)
            file_list.insert(END, f"待转换文件: {file}")


def remove_selected_files():
    global files_to_convert
    if is_converting:
        messagebox.showwarning("警告", "正在转换中，无法移除文件")
        return
    
    selected_indices = list(file_list.curselection())
    if not selected_indices:
        messagebox.showinfo("提示", "请先选择要移除的文件")
        return
    
    for index in sorted(selected_indices, reverse=True):
        if index < len(files_to_convert):
            files_to_convert.pop(index)
            file_list.delete(index)


def start_conversion_async(file_type):
    global is_converting
    if is_converting:
        messagebox.showwarning("提示", "正在转换中，请等待完成")
        return
    
    if not files_to_convert:
        messagebox.showinfo("提示", "请先添加文件到列表")
        return
    
    thread = threading.Thread(target=lambda: do_conversion(file_type), daemon=True)
    thread.start()


def do_conversion(file_type):
    global files_to_convert, if_llm
    
    set_converting_state(True)
    
    files_to_process = files_to_convert.copy()
    total_files = len(files_to_process)
    
    output_resolution = (int(width_entry.get() or 320), int(height_entry.get() or 240))
    video_bitrate = int(bitrate_entry.get() or 0)
    audio_bitrate = int(audio_bitrate_entry.get() or 128)
    framerate = int(framerate_entry.get() or 21)
    try:
        audio_block_size = int(22050 / framerate)
    except ZeroDivisionError:
        audio_block_size = 1050
    audio_quality = 0
    split_video_time = int(spilit_video_time_entry.get() or 300)

    try:
        for i, file in enumerate(files_to_process):
            update_progress(f"正在转换: {i + 1}/{total_files} - {os.path.basename(file)}")
            
            if file_type == 'amv':
                if if_spilit.get() == 1:
                    check_and_split_video(file, output_resolution, video_bitrate, audio_bitrate, audio_block_size, framerate, split_video_time, if_llm)
                else:
                    convert_to_amv(file, output_resolution, video_bitrate, audio_bitrate, audio_block_size, framerate, if_llm)
            elif file_type == 'mp3':
                convert_to_mp3(file, audio_quality)
            elif file_type == 'avi':
                convert_to_avi(file, output_resolution, video_bitrate, audio_bitrate, audio_block_size, framerate)
            elif file_type == 'aac':
                rewrite_aac_single(file)
            elif file_type == '压缩视频(MP4)':
                compress_video(file, output_resolution, framerate, if_use_llm.get())
            elif file_type == '压缩视频(AV1)':
                compress_video_av1(file, output_resolution, framerate, if_use_llm.get())
            else:
                # 使用LLM进行自定义格式转换
                convert_with_llm(file, file_type, output_resolution, video_bitrate, audio_bitrate, framerate)
            
            # 删除源文件
            if if_delete.get() == 1:
                try:
                    if os.path.exists(file):
                        os.remove(file)
                        print(f"源文件 {file} 已删除")
                except Exception as e:
                    print(f"删除文件失败: {e}")

        update_progress(f"转换完成! 共处理 {total_files} 个文件")
        
    except Exception as e:
        update_progress(f"转换出错: {str(e)}")
        print(f"转换错误: {e}")
    
    finally:
        # 清空文件列表
        files_to_convert.clear()
        update_file_list_display()
        set_converting_state(False)


def convert_with_llm(file, file_type, output_resolution, video_bitrate, audio_bitrate, framerate):
    #怎么样这个想法是不是很天才
    prompt=f"请编写一条ffmpeg命令，要求将输入文件转换为\"{file_type}\"格式，输入文件名使用\"{{input_file}}\"代替，输出文件名使用\"{{output_file}}\"代替（注意：输出文件名不包含后缀名，你需要在\"{{output_file}}\"后添加后缀名），用户期望的视频宽度为{output_resolution[0]}、视频高度为{output_resolution[1]}、视频码率为{video_bitrate}k、音频码率为{audio_bitrate}k、帧率为{framerate}，根据实际场景选择是否按照用户期望的数值调整，如果用户期望的数值为0代表此属性由ffmpeg自动转换；{{input_file}}必须用双引号包裹，直接输出纯文本命令，不要使用markdown格式"
    print("debug:当前在convert_with_llm函数中，prompt=",prompt)
    ffmpeg_cmd=llm(prompt)
    for file in files_to_convert:
        if if_use_llm.get()==1:
            base_name = os.path.splitext(os.path.basename(file))[0]
            output_filename = llm_rename(base_name)
        else:
            output_filename = os.path.splitext(file)[0]
        try:
            subprocess.run(ffmpeg_cmd.format(input_file=file, output_file=output_filename), shell=True, check=True)
            print(f"文件输出目录：{os.getcwd()}")
            if if_delete.get() == 1:
                os.remove(file)
                print(f"源文件 {file} 已删除")
        except subprocess.CalledProcessError as e:
            print(f"转换错误: {e}")
    
    return output_filename


def rewrite_aac_single(file):
    if if_use_llm.get() == 1:
        base_name = os.path.splitext(os.path.basename(file))[0]
        output_filename = llm_rename(base_name) + ".m4a"
    else:
        output_filename = os.path.splitext(file)[0] + ".m4a"
    
    output_filename = get_unique_filename(output_filename)
    temp_filename = output_filename.replace('.m4a', '_temp.m4a')

    # 提取元数据与封面
    tags = extract_metadata(file)
    metadata_args = build_metadata_args(tags)
    cover_present = has_cover_art(file)

    def _build_cmd(src):
        if cover_present:
            return (
                f'ffmpeg -y -i "{src}" -i "{src}" '
                f'-map 0:a:0 -map 1:v:0 '
                f'-c:a copy -c:v copy '
                f'-disposition:v:0 attached_pic '
                f'{metadata_args} '
                f'"{output_filename}"'
            )
        else:
            return (
                f'ffmpeg -y -i "{src}" '
                f'-c:a copy -vn '
                f'{metadata_args} '
                f'"{output_filename}"'
            )

    try:
        if os.path.abspath(file) == os.path.abspath(output_filename):
            shutil.copy2(file, temp_filename)
            subprocess.run(_build_cmd(temp_filename), shell=True, check=True)
            os.remove(temp_filename)
        else:
            subprocess.run(_build_cmd(file), shell=True, check=True)
        print(f"AAC/M4A转换完成（含元数据）: {output_filename}")
    except subprocess.CalledProcessError as e:
        print(f"转换错误: {e}")
        if os.path.exists(temp_filename):
            os.remove(temp_filename)


def llm(prompt):
    print("debug:当前在llm函数中，prompt=", prompt)
    try:
        client = OpenAI(
            api_key="114514",
            base_url="https://yvytgpdxdjek.ap-northeast-1.clawcloudrun.com/v1"
        )

        response = client.chat.completions.create(
            model="gemini-3-flash-preview",
            messages=[
                {
                    'role': 'user',
                    'content': f'{prompt}'
                },
            ],
            stream=False
        )

        result_content = response.choices[0].message.content
        print(f"LLM处理结果: {result_content}", end='', flush=True)
        
        return result_content
    except Exception as e:
        if "Rate limit" in str(e):
            print("API速率限制，等待15秒后重试...")
            time.sleep(15)
            return llm(prompt)
        else:
            raise e


def llm_rename(file_to_rename):
    llm_prompt = llm_selected_prompt.get()
    print("debug:llm_prompt=", llm_prompt)
    if llm_prompt == "简化名称":
        prompt = f"请缩短文件名\"{file_to_rename}\"的长度到15个字以内并保留原有含义，去除不重要的无关内容，只输出最后的文件名"
    elif llm_prompt == "歌名-歌手":
        prompt = f"请将文件名\"{file_to_rename}\"更改成类似'歌名 - 歌手'的格式，只输出最后的文件名"
    else:
        prompt = llm_selected_prompt.get() + f"{file_to_rename}"
    print("debug:prompt=", prompt)

    return llm(prompt)


def compress_video(input_file, output_resolution, framerate, if_llm_flag):
    if if_llm_flag == 1:
        base_name = os.path.splitext(os.path.basename(input_file))[0]
        output_filename = llm_rename(base_name) + "_HEVC.mp4"
    else:
        output_filename = os.path.splitext(input_file)[0] + "_HEVC.mp4"
    
    output_filename = get_unique_filename(output_filename)
    
    try:
        ffmpeg_cmd = (
            f'ffmpeg -y -i "{input_file}" '
            f'-vf "scale={output_resolution[0]}:{output_resolution[1]}:force_original_aspect_ratio=decrease:flags=lanczos,format=yuv420p" '
            f'-r {framerate} '
            f'-c:v hevc_nvenc -preset p7 -tune hq '
            f'-rc vbr -b:v 8000k -maxrate 10000k -bufsize 16000k '
            f'-profile:v main '
            f'-spatial-aq 1 -temporal-aq 1 -aq-strength 8 '
            f'-rc-lookahead 32 -multipass 2 -bf 3 -b_ref_mode middle '
            f'-tag:v hvc1 '
            f'-c:a copy "{output_filename}"'
        )
        print("开始压制")
        subprocess.run(ffmpeg_cmd, shell=True, check=True)
        print("完成")
        
    except subprocess.CalledProcessError as e:
        print(f"失败: {e}\n")


def compress_video_av1(input_file, output_resolution, framerate, if_llm_flag):
    if if_llm_flag == 1:
        base_name = os.path.splitext(os.path.basename(input_file))[0]
        output_filename = llm_rename(base_name) + "_AV1.mp4"
    else:
        output_filename = os.path.splitext(input_file)[0] + "_AV1.mp4"
    
    output_filename = get_unique_filename(output_filename)
    
    try:
        ffmpeg_cmd = (
            f'ffmpeg -y -i "{input_file}" '
            f'-vf "scale={output_resolution[0]}:{output_resolution[1]}:force_original_aspect_ratio=decrease:flags=lanczos,format=yuv420p" '
            f'-r {framerate} '
            f'-c:v av1_nvenc -preset p7 -tune hq '
            f'-rc vbr -b:v 6000k -maxrate 8000k -bufsize 12000k '
            f'-spatial-aq 1 -temporal-aq 1 -aq-strength 8 '
            f'-rc-lookahead 32 -multipass 2 '
            f'-tag:v av01 '
            f'-c:a copy "{output_filename}"'
        )
        
        print(f"开始AV1压制 : {input_file}")
        subprocess.run(ffmpeg_cmd, shell=True, check=True)
        print(f"AV1压制完成: {output_filename}")

    except subprocess.CalledProcessError as e:
        print(f"压制出错: {e}")


# 创建 GUI
root = Tk()
root.title("baw_tool_kit")
author = "竹和木 or Olenxane"
print(f"正在启动...developed by {author}")

# 参数设置部分
if_spilit = IntVar()
if_delete = IntVar()
if_rotate = IntVar()
if_use_llm = IntVar()

param_setting_frame = Frame(root)
param_setting_frame.pack(pady=10)

width_label = Label(param_setting_frame, text="宽度:")
width_label.grid(row=0, column=0)
width_entry = Entry(param_setting_frame)
width_entry.insert(0, "320")
width_entry.grid(row=0, column=1)

height_label = Label(param_setting_frame, text="高度:")
height_label.grid(row=0, column=2)
height_entry = Entry(param_setting_frame)
height_entry.insert(0, "240")
height_entry.grid(row=0, column=3)

bitrate_label = Label(param_setting_frame, text="视频码率(kbps):")
bitrate_label.grid(row=1, column=0)
bitrate_entry = Entry(param_setting_frame)
bitrate_entry.insert(0, "700")
bitrate_entry.grid(row=1, column=1)

audio_bitrate_label = Label(param_setting_frame, text="音频码率(kbps):")
audio_bitrate_label.grid(row=1, column=2)
audio_bitrate_entry = Entry(param_setting_frame)
audio_bitrate_entry.insert(0, "192")
audio_bitrate_entry.grid(row=1, column=3)

file_type_label = Label(param_setting_frame, text="输出文件格式")
file_type_label.grid(row=3, column=0)
options = ["amv", "mp3", "aac", "avi", "自动转换(直接输入格式)", "压缩视频(MP4)", "压缩视频(AV1)"]
selected_option = StringVar()
file_type_entry = ttk.Combobox(param_setting_frame, textvariable=selected_option, values=options)
file_type_entry.set(options[0])
file_type_entry.grid(row=3, column=1)

framerate_label = Label(param_setting_frame, text="帧率:")
framerate_label.grid(row=3, column=2)
framerate_entry = Entry(param_setting_frame)
framerate_entry.insert(0, "0")
framerate_entry.grid(row=3, column=3)

llm_prompt_label = Label(param_setting_frame, text="llm提示词类型")
llm_prompt_label.grid(row=4, column=0)
prompt_options = ["简化名称", "歌名-歌手", "自定义"]
llm_selected_prompt = StringVar()
llm_prompt = ttk.Combobox(param_setting_frame, textvariable=llm_selected_prompt, values=prompt_options)
llm_prompt.set(prompt_options[0])
llm_prompt.grid(row=4, column=1)

spilit_video_time = Label(param_setting_frame, text="视频切割时间")
spilit_video_time.grid(row=4, column=2)
spilit_video_time_entry = Entry(param_setting_frame)
spilit_video_time_entry.insert(0, "300")
spilit_video_time_entry.grid(row=4, column=3)

spilit_video = Checkbutton(root, text="是否启用视频切割", variable=if_spilit)
spilit_video.pack()

delete_source_file = Checkbutton(root, text="是否删除源文件", variable=if_delete)
delete_source_file.pack()

rotate_video = Checkbutton(root, text="是否旋转视频(逆时针90°)", variable=if_rotate)
rotate_video.pack()

open_biliout = Button(root, text="打开b站缓存提取工具", command=biliout2.main)
open_biliout.pack()

use_llm = Checkbutton(root, text="是否使用llm", variable=if_use_llm)
use_llm.pack()


def on_select():
    file_type = selected_option.get()
    start_conversion_async(file_type)


# 文件选择和转换部分
file_list = Listbox(root, width=80, height=15, selectmode=EXTENDED)
file_list.pack(pady=10)

button_frame = Frame(root)
button_frame.pack(pady=5)

add_files_button = Button(button_frame, text="添加文件到列表", command=add_files_to_list, width=20)
add_files_button.pack(side='left', padx=5)

remove_files_button = Button(button_frame, text="移除选中文件", command=remove_selected_files, width=20)
remove_files_button.pack(side='left', padx=5)

convert_button = Button(root, text="开始转换", command=on_select)
convert_button.pack(pady=5)

progress_label = Label(root, text="就绪", fg="blue", font=("Arial", 10))
progress_label.pack(pady=5)

root.mainloop()
