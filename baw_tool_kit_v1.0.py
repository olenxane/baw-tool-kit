import os
import subprocess
from tkinter import *
from tkinter import filedialog, Tk, Frame, Button, Listbox, Label, Entry, END
from tkinter import ttk
import biliout2
from datetime import timedelta
import shutil
from openai import OpenAI
import time

files_to_convert = []
if_llm=True

def check_and_split_video(selected_file,output_resolution, video_bitrate, audio_bitrate, audio_block_size, framerate,split_video_time,if_llm):
    # 使用ffprobe检查视频时长
    ffprobe_cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{selected_file}"'
    result = subprocess.run(ffprobe_cmd, shell=True, capture_output=True, text=True)
    duration_seconds = float(result.stdout)

    if duration_seconds <= split_video_time:
        # 如果视频时长小于等于300秒，不进行分割
        convert_to_amv(selected_file, output_resolution, video_bitrate, audio_bitrate, audio_block_size, framerate,if_llm)
        return [selected_file]

    # 计算需要分割的段数
    num_segments = int(duration_seconds) // split_video_time + (1 if duration_seconds % split_video_time > 0 else 0)
    split_files = []  # 用于存储分割后的文件路径，以便后续处理和删除
    amv_files = []    # 存储生成的AMV文件路径

    # 处理基础文件名
    if if_use_llm.get()==1:
        if_llm=False
        base_name = llm(os.path.splitext(os.path.basename(selected_file))[0])
    else:
        if_llm=True
        base_name = os.path.splitext(os.path.basename(selected_file))[0]

    # 分割视频
    for i in range(num_segments):
        start_time = timedelta(seconds=i*split_video_time)
        end_time = timedelta(seconds=(i+1)*split_video_time)
        
        output_file = f"{base_name}_{i+1}{os.path.splitext(selected_file)[1]}"
        split_files.append(output_file)
        
        ffmpeg_cmd = f'ffmpeg -i "{selected_file}" -ss {start_time} -to {end_time} -c copy "{output_file}"'
        subprocess.run(ffmpeg_cmd, shell=True)
        
        # 转换分割后的文件并记录AMV路径
        amv_path = convert_to_amv(output_file, output_resolution, video_bitrate, audio_bitrate, audio_block_size, framerate,if_llm)
        amv_files.append(amv_path)

    for file in split_files:
        os.remove(file)

    return amv_files  # 返回生成的AMV文件列表

def convert_to_amv(input_file, output_resolution, video_bitrate, audio_bitrate, audio_block_size, framerate,if_llm):
    if if_use_llm.get()==1 and if_llm:
        base_name = os.path.splitext(os.path.basename(input_file))[0]
        output_filename = llm(base_name) + ".amv"
        print("成功调用llm:",output_filename)
    else:
        if if_spilit.get() == 1:
            base_name = os.path.splitext(os.path.basename(input_file))[0]
            output_filename = base_name + ".amv"
        else:
            output_filename = os.path.splitext(input_file)[0] + ".amv"
    
    if if_rotate.get()==1:
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
    if if_use_llm.get()==1:
        base_name = os.path.splitext(os.path.basename(input_file))[0]
        output_filename = llm(base_name) + ".mp3"
    else:
        output_filename = os.path.splitext(input_file)[0] + ".mp3"

    try:
        ffmpeg_cmd_mp3 = f'ffmpeg -i "{input_file}" -vn -c:a libmp3lame -q:a {audio_quality} "{output_filename}"'
        subprocess.run(ffmpeg_cmd_mp3, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"转换错误: {e}")

def convert_to_avi(input_file, output_resolution, video_bitrate, audio_bitrate, audio_block_size, framerate):
    if if_use_llm.get()==1:
        base_name = os.path.splitext(os.path.basename(input_file))[0]
        output_filename = llm(base_name) + ".avi"
    else:
        output_filename = os.path.splitext(input_file)[0] + ".avi"
    
    try:
        ffmpeg_cmd_avi = f'ffmpeg -i "{input_file}" -s {output_resolution[0]}x{output_resolution[1]} -b:v {video_bitrate}k -b:a {audio_bitrate}k -r {framerate} "{output_filename}"'
        subprocess.run(ffmpeg_cmd_avi, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"转换错误: {e}")
    
    return output_filename

def more_conversion(input_file, file_type):
    output_filename = os.path.splitext(input_file)[0]+'.'+file_type
    try:
        ffmpeg_cmd = f'ffmpeg -i "{input_file}" "{output_filename}"'
        subprocess.run(ffmpeg_cmd, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"转换错误: {e}")

def select_files():
    root = Tk()
    root.withdraw()
    input_files = filedialog.askopenfilenames(title="选择要转换的文件", filetypes=(("所有文件", "*.*"),("文件", "*.mp4;*.mp3;*.wav;*.aac;*.amv;*.m4s;*.avi")))
    root.destroy()
    return input_files

def add_files_to_list():
    global files_to_convert
    selected_files = select_files()
    for file in selected_files:
        files_to_convert.append(file)
        file_list.insert(END, f"待转换文件: {file}")

def start_conversion(file_type):
    global files_to_convert
    if not files_to_convert:
        print("请先添加文件到列表")
        return

    output_resolution = (int(width_entry.get() or 320), int(height_entry.get() or 240))
    video_bitrate = int(bitrate_entry.get() or 0)
    audio_bitrate = int(audio_bitrate_entry.get() or 128)
    framerate = int(framerate_entry.get() or 21)
    audio_block_size = int(22050 / framerate)
    audio_quality = 0
    split_video_time=int(spilit_video_time_entry.get() or 300)

    if file_type == 'amv':
        if if_spilit.get() == 1:
            for file in files_to_convert:
                check_and_split_video(file,output_resolution, video_bitrate, audio_bitrate, audio_block_size, framerate, split_video_time,if_llm)
        else:
            for file in files_to_convert:
                convert_to_amv(file, output_resolution, video_bitrate, audio_bitrate, audio_block_size, framerate, if_llm)
    elif file_type == 'mp3':
        for file in files_to_convert:
            convert_to_mp3(file, audio_quality)
    elif file_type == 'avi':
        for file in files_to_convert:
            convert_to_avi(file, output_resolution, video_bitrate, audio_bitrate, audio_block_size, framerate)
    elif file_type=='aac':
        for file in files_to_convert:
            rewrite_aac(file)
    elif file_type == '压缩视频':
        for file in files_to_convert:
            compress_video(file, output_resolution, framerate, if_use_llm.get())
    elif file_type=='压缩视频（AV1模式）':
        for file in files_to_convert:
            compress_video_av1(file, output_resolution, framerate, if_use_llm.get())
    else:
        for file in files_to_convert:
            more_conversion(file, file_type)

    # 清空文件列表
    if if_delete.get()==1:
        for delete_file in files_to_convert:
            os.remove(delete_file)
    files_to_convert = []
    file_list.delete(0, END)

def rewrite_aac(file):
    for file in files_to_convert:
        if if_use_llm.get()==1:
            base_name = os.path.splitext(os.path.basename(file))[0]
            output_filename = llm(base_name) + ".aac"
        else:
            output_filename = os.path.splitext(file)[0] + ".aac"
        temp_filename=output_filename.replace('.aac','_temp.aac')
        try:
            if if_delete.get() == 1:
                shutil.copy2(file, temp_filename)
                os.remove(file)
                ffmpeg_cmd = f'ffmpeg -y -i "{temp_filename}" -c:a copy "{output_filename}"'
                subprocess.run(ffmpeg_cmd, shell=True, check=True)
                os.remove(temp_filename)
                print(f"源文件 {file} 已删除")
            else:
                ffmpeg_cmd = f'ffmpeg -i "{file}" -c:a copy "{output_filename}"'
                subprocess.run(ffmpeg_cmd, shell=True, check=True)
        except subprocess.CalledProcessError as e:
            print(f"转换错误: {e}")
    files_to_convert.clear()
    file_list.delete(0, END)

def llm(file_to_rename):
    llm_prompt=llm_selected_prompt.get()
    if llm_prompt=="简化名称":
        llm_prompt=f"请缩短文件名{file_to_rename}的长度到15个字以内并保留原有含义，去除不重要的无关内容，只输出最后的文件名"
    elif llm_prompt=="歌名-歌手":
        llm_prompt=f"请将文件名{file_to_rename}更改成类似'歌名 - 歌手'的格式，只输出最后的文件名"
    else:
        llm_prompt=llm_selected_prompt.get()+f"{file_to_rename}"
    #填写API Key
    try:
        client = OpenAI(
        api_key="",
        base_url="")

        response = client.chat.completions.create(
            model="gemini-2.5-flash-preview-09-2025",
            messages=[
                {
                    'role': 'user',
                    'content': f'{llm_prompt}'
                },
            ],
            stream=False
        )

        # 直接获取响应内容并返回
        result_content = response.choices[0].message.content
        print(f"LLM处理结果: {result_content}", end='', flush=True)
        
        return result_content
    #处理API速率限制导致的异常，等待15秒后重新请求
    except Exception as e:
        if "Rate limit" in str(e):
            print("API速率限制，等待15秒后重试...")
            time.sleep(15)
            return llm(file_to_rename)  # 递归调用自身
        else:
            raise e  # 其他错误则抛出
        
def compress_video(input_file, output_resolution, framerate, if_llm):
    if if_llm == 1:
        base_name = os.path.splitext(os.path.basename(input_file))[0]
        output_filename = llm(base_name) + "_HEVC.mp4"
    else:
        output_filename = os.path.splitext(input_file)[0] + "_HEVC.mp4"
    
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

def compress_video_av1(input_file, output_resolution, framerate, if_llm):
    if if_llm == 1:
        base_name = os.path.splitext(os.path.basename(input_file))[0]
        output_filename = llm(base_name) + "_AV1.mp4"
    else:
        output_filename = os.path.splitext(input_file)[0] + "_AV1.mp4"
    
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

# 参数设置部分
if_spilit=IntVar()
if_delete=IntVar()
if_rotate=IntVar()
if_use_llm=IntVar()


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
bitrate_entry.insert(0, 0)
bitrate_entry.grid(row=1, column=1)
audio_bitrate_label = Label(param_setting_frame, text="音频码率(kbps):")
audio_bitrate_label.grid(row=1, column=2)
audio_bitrate_entry = Entry(param_setting_frame)
audio_bitrate_entry.insert(0, "128")
audio_bitrate_entry.grid(row=1, column=3)
file_type_label = Label(param_setting_frame, text="输出文件格式")
file_type_label.grid(row=3, column=0)
options = ["amv", "mp3", "aac", "avi", "更多(请输入)", "压缩视频", "压缩视频（AV1模式）"]
selected_option = StringVar()
file_type_entry = ttk.Combobox(param_setting_frame, textvariable=selected_option, values=options)
file_type_entry.set(options[0])
file_type_entry.grid(row=3, column=1)
framerate_label = Label(param_setting_frame, text="帧率:")
framerate_label.grid(row=3, column=2)
framerate_entry = Entry(param_setting_frame)
framerate_entry.insert(0, "21")
framerate_entry.grid(row=3, column=3)
llm_prompt_label = Label(param_setting_frame, text="llm提示词类型")
llm_prompt_label.grid(row=4, column=0)
prompt_options = ["简化名称", "歌名-歌手", "自定义"]
llm_selected_prompt = StringVar()
llm_prompt = ttk.Combobox(param_setting_frame, textvariable=llm_selected_prompt, values=prompt_options)
llm_prompt.set(prompt_options[0])
llm_prompt.grid(row=4, column=1)
spilit_video_time=Label(param_setting_frame,text="视频切割时间")
spilit_video_time.grid(row=4, column=2)
spilit_video_time_entry = Entry(param_setting_frame)
spilit_video_time_entry.insert(0, "300")
spilit_video_time_entry.grid(row=4, column=3)
spilit_video=Checkbutton(root,text="是否启用视频切割",variable=if_spilit)
spilit_video.pack()
delete_source_file=Checkbutton(root,text="是否删除源文件",variable=if_delete)
delete_source_file.pack()
rotate_video=Checkbutton(root,text="是否旋转视频(逆时针90°)",variable=if_rotate)
rotate_video.pack()
open_biliout=Button(root,text="打开b站缓存提取工具",command=biliout2.main)
open_biliout.pack()
use_llm=Checkbutton(root,text="是否使用llm",variable=if_use_llm)
use_llm.pack()

def on_select():
    file_type = selected_option.get()
    start_conversion(file_type)

# 文件选择和转换部分
file_list = Listbox(root, width=80, height=15)
file_list.pack(pady=10)
add_files_button = Button(root, text="添加文件到列表", command=add_files_to_list, width=20)
add_files_button.pack(pady=5)
convert_button = Button(root, text="开始转换", command=on_select)
convert_button.pack(pady=5)

root.mainloop()

