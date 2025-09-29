每天定时任务跑一下：    
    python3 -u traccar_report.py    
自动从Traccar服务器数据库中查找前一天有更新（lastupdate）信息的设备，并将该设备上一天的全部活动绘在一张地图上，然后邮件附带地图的图片和原始HTML文件    

需要用到的python包：    
  pip install selenium pandas folium pymysql smtplib    

      
