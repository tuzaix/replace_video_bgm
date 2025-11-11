#!/bin/bash

bin=`dirname "$0"`
bin=`cd $bin; pwd`

echo "install ffmpeg7"

sudo apt install snapd
sudo add-apt-repository ppa:ubuntuhandbook1/ffmpeg7
sudo apt update
sudo apt install ffmpeg
ffmpeg -version

echo "finished install ffmpeg7"

echo "==============install ftp server================="
sudo apt update
sudo apt install vsftpd
sudo systemctl start vsftpd
sudo systemctl enable vsftpd
sudo systemctl status vsftpd
sudo ufw allow 20/tcp
sudo ufw allow 21/tcp
# 允许被动模式数据连接的端口范围
sudo ufw allow 40000:50000/tcp
sudo ufw reload
sudo cp /etc/vsftpd.conf /etc/vsftpd.conf.original

cat > /etc/vsftpd.conf << 'EOF'
listen=NO
listen_ipv6=YES
anonymous_enable=NO
local_enable=YES
write_enable=YES
dirmessage_enable=YES
use_localtime=YES
xferlog_enable=YES
connect_from_port_20=YES
chroot_local_user=YES
allow_writeable_chroot=YES
write_enable=YES
secure_chroot_dir=/var/run/vsftpd/empty
pam_service_name=vsftpd
pasv_min_port=40000
pasv_max_port=50000
EOF

sudo systemctl restart vsftpd
sudo systemctl status  vsftpd
echo "==========success install ftp server==============="
sudo timedatectl set-timezone Asia/Shanghai
/usr/bin/chmod -R 777 /home/ftpuser_hostinger

sudo useradd -m -s /bin/bash work
sudo passwd work
