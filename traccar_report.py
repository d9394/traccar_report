import pymysql
import pandas as pd
from datetime import datetime, timedelta
import folium
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage # 用于 PNG 图片
from email.mime.application import MIMEApplication # 用于 HTML 文件
import time
from typing import Optional # 引入类型提示
import requests # 用于 HTTP 请求

# --- Selenium 导入 ---
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
# 假设 'chromium-chromedriver' 在系统 PATH 中可被找到

# --- 配置常量 ---

# 1. Traccar MySQL 数据库配置
DB_CONFIG = {
    "host": "127.0.0.1",
    "user": "traccar",
    "password": "123456",
    "database": "traccar",
    "port": 3306 , # MySQL 默认端口
}

# 2. 邮件配置
EMAIL_CONFIG = {
    "smtp_server": "127.0.0.1",  # 例如: smtp.gmail.com
    "smtp_port": 25,                         # 或 465 (取决于提供商)
    "smtp_username": "test",
    "smtp_password": "123456", # 推荐使用应用专用密码
    "recipient_email": "test@test.com"
}

# 3. 文件路径配置
OUTPUT_DIR = "/dev/shm/traccar_reports"


# 高度范围 (meters)
MIN_ALTITUDE = 0
MAX_ALTITUDE = 200 # 假设最高高度为 200 米，您可以根据实际情况调整
BASE_ICON_SIZE = 20
MAX_SIZE_INCREASE = 15

# 速度范围 (knots)
MAX_SPEED = 50.0  # 假设最高速度为 50 节
# 红色亮度通道的范围控制：
MIN_RED_VALUE = 40  # 速度快时的最低红色亮度 (最暗/最浅)
MAX_RED_VALUE = 255 # 速度慢时的最高红色亮度 (最亮/最鲜艳)

HTTP_NOTIFICATION_URL = "http://192.168.1.1:80"
HTTP_NOTIFICATION_USER = 'test'

# === 重点：添加代理配置 ===
PROXY_SERVER = None  # 如果不使用PROXY，则PROXY_SERVER = None，使用格式：PROXY_SERVER = IP:PORT 协议默认为：http://IP:PORT

# --- 辅助函数：数据库和日期处理 (代码不变) ---

def send_http_notification(png_path, device_name):
    """
    使用 requests 库发送 HTTP POST 请求，上传 PNG 文件。
    """
    # 1. 准备表单数据 (模拟 curl -F 参数)
    data = {
        "usr": HTTP_NOTIFICATION_USER,
        "from": "Traccar",
        "msg": f"Report for {device_name}: {os.path.basename(png_path)}",
    }
    
    # 2. 准备文件数据 (模拟 curl -F file=@...)
    # requests 要求文件以 (文件名, 文件数据) 的格式提供
    # 我们以二进制读取模式 ('rb') 打开文件
    try:
        with open(png_path, 'rb') as f:
            files = {
                'file': (os.path.basename(png_path), f)
            }
            
            print(f"Executing HTTP POST to {HTTP_NOTIFICATION_URL} with file {os.path.basename(png_path)}...")
            
            # 3. 发送请求，设置超时为 300 秒
            response = requests.post(
                HTTP_NOTIFICATION_URL, 
                data=data, 
                files=files,
                timeout=300 
            )

            # 4. 检查响应结果
            if response.status_code == 200:
                print(f"HTTP POST success (Status: 200). Response: {response.text.strip()}")
            else:
                print(f"HTTP POST failed (Status: {response.status_code}). Response: {response.text.strip()}")
                
    except requests.exceptions.Timeout:
        print("HTTP POST failed: Request timed out after 300 seconds.")
    except requests.exceptions.ConnectionError:
        print(f"HTTP POST failed: Could not connect to {HTTP_NOTIFICATION_URL}. Check network and server status.")
    except FileNotFoundError:
        print(f"HTTP POST failed: PNG file not found at {png_path}.")
    except Exception as e:
        print(f"An unexpected error occurred during HTTP POST: {e}")

def get_report_time_range(target_date_str: Optional[str] = None):
    """
    计算报告的起始和结束时间。
    
    参数:
        target_date_str (str, optional): 目标日期字符串 (格式: YYYY-MM-DD)。
                                          如果为 None，则返回昨天的日期范围。
                                          
    返回:
        tuple: (start_time, end_time, date_str)
    """
    if target_date_str:
        try:
            # 尝试解析传入的日期字符串
            report_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
            print(f"DEBUG MODE: Using specified date: {report_date.isoformat()}")
        except ValueError:
            print(f"Error: Invalid date format '{target_date_str}'. Falling back to yesterday.")
            report_date = datetime.now().date() - timedelta(days=1)
    else:
        # 默认模式：处理昨天
        report_date = datetime.now().date() - timedelta(days=1)
        print("NORMAL MODE: Using yesterday's date.")
        
    start_time = datetime.combine(report_date, datetime.min.time())
    end_time = datetime.combine(report_date, datetime.max.time())
    date_str = report_date.isoformat()
    
    return start_time, end_time, date_str

def execute_query(sql, params=None):
    """通用的数据库查询函数"""
    conn = None
    try:
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return cursor.fetchall()
    except Exception as e:
        print(f"Database error: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_device_data(device_id, start_time, end_time):
    """查询指定设备的上一天所有位置数据，包括 altitude 和 speed"""
    # SQL 增加 altitude 和 speed
    sql = """
        SELECT latitude, longitude, course, fixTime, altitude, speed
        FROM tc_positions
        WHERE deviceid = %s
          AND fixTime >= %s AND fixTime < %s
        ORDER BY fixTime ASC;
    """
    data = execute_query(sql, (device_id, start_time, end_time))
    
    if not data:
        return pd.DataFrame()

    # DataFrame 的列名也相应增加
    df = pd.DataFrame(data, columns=['latitude', 'longitude', 'course', 'fixTime', 'altitude', 'speed'])
    return df

# --- 核心函数：地图转换 (配置已集成) ---

def html_to_png(html_path, png_path):
    """
    使用 Selenium 将 Folium HTML 地图渲染成 PNG 图片。
    所有 Selenium 配置都在函数内部完成。
    """
    driver = None
    try:
        print("Starting Chromium headless browser...")
        
        # 1. 配置 Chrome Options
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")
        
        if (PROXY_SERVER) :
            # 使用 --proxy-server 命令行参数来指定代理地址
            chrome_options.add_argument(f'--proxy-server={PROXY_SERVER}')
            print(f"Configured Chromium to use proxy: http://{PROXY_SERVER}")
            # ==========================
        
        # 2. 配置 Service 并增加超时时间 (应对启动慢的问题)
        chrome_service = Service() 
        chrome_service.creation_timeout = 300  # 增加启动超时到 5 分钟
        
        # 3. 初始化 WebDriver
        driver = webdriver.Chrome(service=chrome_service, options=chrome_options)
        
        # 4. 加载本地 HTML 文件
        driver.get(f"file:///{os.path.abspath(html_path)}")
        
        # 给予地图加载和渲染的时间
        time.sleep(15) 
        
        # 4. 截图并保存为 PNG
        driver.save_screenshot(png_path)
        print(f"Screenshot saved: {png_path}")
        
    except Exception as e:
        print(f"Error during Selenium (Chromium) conversion: {e}")
        print("ACTION REQUIRED: Ensure 'chromium-chromedriver' is installed and accessible in the system PATH.")
        return None
    finally:
        # 确保关闭浏览器
        if driver:
            driver.quit()
        
    return png_path

def get_color(speed):
    """
    根据速度返回纯红色系的颜色：
    - 速度快: R 接近 MIN_RED_VALUE -> 颜色暗淡 (暗红)
    - 速度慢: R 接近 MAX_RED_VALUE -> 颜色鲜艳 (鲜红)
    """
    
    # 1. 将速度归一化到 [0, 1] 范围
    normalized_speed = min(speed, MAX_SPEED) / MAX_SPEED
    
    # 2. 关键：反转归一化速度 (1 - normalized_speed)
    #   - 速度快 (≈1) -> 反转后 ≈ 0
    #   - 速度慢 (≈0) -> 反转后 ≈ 1
    inverse_speed = 1 - normalized_speed
    
    # 3. 将反转后的值映射到红色亮度范围 [MIN_RED_VALUE, MAX_RED_VALUE]
    # R 值随着 inverse_speed (即速度的减慢) 而增加
    R = int(MIN_RED_VALUE + inverse_speed * (MAX_RED_VALUE - MIN_RED_VALUE))
    
    # 4. 保持 G 和 B 通道为 0
    G = 0 
    B = 0 
    
    # 返回十六进制颜色代码
    return f'#{R:02x}{G:02x}{B:02x}'

def get_icon_size(altitude):
    """根据高度返回图标大小 (在 BASE_ICON_SIZE 和 MAX_SIZE_INCREASE 之间变化)"""
    # 将高度归一化到 [0, 1] 范围
    normalized_altitude = min(max(altitude, MIN_ALTITUDE), MAX_ALTITUDE) / (MAX_ALTITUDE - MIN_ALTITUDE)
    
    # 计算额外的尺寸
    size_increase = MAX_SIZE_INCREASE * normalized_altitude
    
    # 最终尺寸
    final_size = BASE_ICON_SIZE + size_increase
    
    # Folium DivIcon 需要 (宽度, 高度)
    return (int(final_size), int(final_size))

# --- 核心函数：地图绘制 (代码不变) ---

def create_track_map(df, device_name, date_str):
    """绘制轨迹地图，应用速度颜色和高度大小，并保存为 PNG"""
    if df.empty:
        print(f"No data to plot for device {device_name}.")
        return None, None
    
    points = df[['latitude', 'longitude']].values.tolist()
    
    # 初始化地图时，不再使用固定 start_point 和 zoom_start
    # m = folium.Map(location=start_point, zoom_start=14)
    # 先创建一个空的地图，或者以第一个点为中心，zoom_start 可以小一点
    m = folium.Map(location=points[0], zoom_start=8) # 初始 zoom_start 可以设小一点

    # 绘制轨迹线 (不变)
    folium.PolyLine(points, color="#4682B4", weight=3, opacity=0.8).add_to(m)

    # 遍历添加带方向、速度颜色和高度大小的标记 (不变)
    for _, row in df.iterrows():
        lat, lon = row['latitude'], row['longitude']
        course = row['course'] if row['course'] is not None else 0
        speed = row['speed'] if row['speed'] is not None else 0
        altitude = row['altitude'] if row['altitude'] is not None else 0
        
        fix_time = row['fixTime'].strftime('%Y-%m-%d %H:%M:%S')

        marker_color = get_color(speed)
        icon_size = get_icon_size(altitude)

        css_angle = (course-90) % 360
        
        svg_icon = f"""
        <div style="transform: rotate({css_angle}deg); color: {marker_color}; font-size: {icon_size[0]}px; line-height: 1;">
            &#x27a4; 
        </div>
        """
        popup_html = f"Time: {fix_time}<br>Speed: {speed:.1f} kn<br>Altitude: {altitude:.1f} m<br>Direction: {course}°"

        folium.Marker(
            [lat, lon],
            popup=popup_html,
            icon=folium.DivIcon(
                html=svg_icon,
                icon_size=icon_size,
                icon_anchor=(icon_size[0] // 2, icon_size[1] // 2)
            )
        ).add_to(m)

    # === 关键：使用 fit_bounds 自动调整地图视图 ===
    # bounds 是一个 (min_latitude, min_longitude, max_latitude, max_longitude) 元组
    # 或者直接使用 [(lat1, lon1), (lat2, lon2), ...] 列表
    m.fit_bounds([[df['latitude'].min(), df['longitude'].min()], 
                  [df['latitude'].max(), df['longitude'].max()]])
    # ===============================================

    # ... (后续保存 HTML、转换为 PNG、清理临时文件等代码不变) ...
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    html_filename = os.path.join(OUTPUT_DIR, f"temp_track_{device_name}_{date_str}.html")
    m.save(html_filename)
    
    png_filename = os.path.join(OUTPUT_DIR, f"track_report_{device_name}_{date_str}.png")
    png_path = html_to_png(html_filename, png_filename)
    
    return png_path, html_filename 

# --- 核心函数：邮件发送 (代码不变) ---

def add_attachment(msg: MIMEMultipart, file_path: str):
    """根据文件扩展名添加附件，使用正确的 MIME 类型"""
    if not os.path.exists(file_path):
        print(f"Attachment file not found: {file_path}")
        return

    file_name = os.path.basename(file_path)
    
    try:
        with open(file_path, "rb") as f:
            file_data = f.read()
            
            # 根据文件扩展名确定 MIME 类型
            if file_name.lower().endswith('.png'):
                # 图片附件
                maintype = 'image'
                subtype = 'png'
                attach = MIMEImage(file_data, _subtype=subtype)
            elif file_name.lower().endswith('.html'):
                # HTML 附件
                maintype = 'application'
                subtype = 'html'
                attach = MIMEApplication(file_data, _subtype=subtype)
            else:
                # 默认处理方式 (例如，如果将来增加其他类型)
                maintype = 'application'
                subtype = 'octet-stream'
                attach = MIMEApplication(file_data, _subtype=subtype)

            attach.add_header('Content-Disposition', 'attachment', filename=file_name)
            msg.attach(attach)
            print(f"Attached file: {file_name}")
            
    except Exception as e:
        print(f"Error attaching file {file_name}: {e}")

def send_report_email(recipient, device_name, date_str, attachment_paths: list):
    """发送带有附件列表的邮件"""
    
    msg = MIMEMultipart()
    msg['From'] = EMAIL_CONFIG['smtp_username']
    msg['To'] = recipient
    msg['Subject'] = f"Traccar 设备轨迹报告 - {device_name} ({date_str})"
    
    # 邮件正文
    body = (
        f"附件是设备 '{device_name}' 在 {date_str} 的轨迹报告。\n\n"
        f"1. PNG 文件可直接预览。\n"
        f"2. HTML 文件包含完整的交互式地图，请下载后用浏览器打开。"
    )
    msg.attach(MIMEText(body, 'plain', 'utf-8'))
    
    # 遍历并添加所有附件
    for path in attachment_paths:
        add_attachment(msg, path)
        
    # 发送邮件
    try:
        server = smtplib.SMTP(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port'])
        server.starttls()
        server.login(EMAIL_CONFIG['smtp_username'], EMAIL_CONFIG['smtp_password'])
        server.sendmail(EMAIL_CONFIG['smtp_username'], recipient, msg.as_string())
        server.quit()
        print(f"Email report sent successfully for device {device_name}.")
    except Exception as e:
        print(f"Error sending email for device {device_name}: {e}")

# --- 主执行逻辑 (代码不变) ---

def main():
    # --- 调试开关 (仅用于测试) ---
    # 要调试特定日期，请取消注释并修改日期。
    # 例如，测试 2023 年 10 月 26 日的数据：
    # DEBUG_DATE = "2023-10-26" 
    
    # 正常运行时，保持注释状态或设为 None
    DEBUG_DATE = None 
    # ---------------------------

    # 1. 获取时间范围
    # 注意：现在调用的是 get_report_time_range
    start_time, end_time, date_str = get_report_time_range(DEBUG_DATE)
    print(f"Processing data for: {date_str} ({start_time} to {end_time})")

    # 2. 查询上一天有更新的设备 ID 和名称 (使用优化的 SQL)
    sql_devices = """
        SELECT id, name
        FROM tc_devices
        WHERE lastupdate >= %s AND lastupdate < %s;
    """

    devices_updated = execute_query(sql_devices, (start_time, end_time))

    if not devices_updated:
        print("No devices updated yesterday. Exiting.")
        return

    print(f"Found {len(devices_updated)} devices updated.")

    # 3. 遍历设备并生成报告
    for device_id, device_name in devices_updated:
        print(f"--- Processing device ID: {device_id} ({device_name}) ---")
        
        # 4. 获取位置数据
        df = get_device_data(device_id, start_time, end_time)
        
        if df.empty:
            print(f"No position data found for {device_name}. Skipping.")
            continue
            
        # 5. 绘制地图并转换为 PNG
        png_path, html_path = create_track_map(df, device_name, date_str)
        
        # 6. 发送邮件
        # === 修正点 2: 检查是否有有效的 PNG 路径和 HTML 路径 ===
        if png_path and html_path and os.path.exists(png_path) and os.path.exists(html_path):
            attachment_paths = [png_path, html_path]
            
            send_report_email(
                EMAIL_CONFIG['recipient_email'],
                device_name,
                date_str,
                attachment_paths
            )
            
            # 7. 执行 HTTP POST 请求，上传 PNG 文件
            send_http_notification(png_path, device_name) 
            
            # --- 清理生成的文件 ---
            try:
                os.remove(png_path)
                os.remove(html_path)
                print(f"Cleaned up temporary files for {device_name}.")
            except OSError as e:
                print(f"Error cleaning up files: {e}")
        else:
             # 如果任何一个路径无效，跳过邮件发送和清理
             print(f"Skipping email for {device_name} due to missing attachments.")

if __name__ == "__main__":
    main()
