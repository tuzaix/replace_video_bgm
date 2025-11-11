#!/bin/bash

set -e  # 遇到任何错误立即退出

# -------------------------- 变量定义 ---------------------------
# 定义FTP的根目录
FTP_BASE="/home/ftp"
# 定义共享用户组名，所有新建的FTP用户和work用户都会加入此组
SHARED_GROUP="ftp_shared_workgroup"
# 随机密码长度
PASSWORD_LENGTH=12
# 拥有特殊权限的work用户名
WORK_USER="work"
# 新用户名，通过脚本参数传入
NEW_USER="$1"

# vsftpd 配置文件路径
VSFTPD_CONF="/etc/vsftpd.conf"
# 用户禁锢列表文件路径
CHROOT_LIST_FILE="/etc/vsftpd.chroot_list"

# -------------------------- 函数定义 ---------------------------

# 检查是否以root权限运行
check_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "错误：此脚本必须以 root 权限运行。请使用 sudo 执行。"
        exit 1
    fi
}

# 检查必要参数
check_arguments() {
    if [ $# -ne 1 ]; then
        echo "用法: $0 <新用户名>"
        exit 1
    fi
}

# 检查用户是否已存在
check_user_existence() {
    if id "$1" &>/dev/null; then
        echo "错误：用户 '$1' 已存在。"
        exit 1
    fi
}

# 检查依赖命令
check_dependencies() {
    if ! command -v openssl &> /dev/null; then
        echo "错误：未找到 'openssl' 命令。请先安装它。"
        exit 1
    fi
}

# 安装并配置vsftpd
setup_vsftpd() {
    # 检查vsftpd是否已安装
    if ! dpkg -l | grep -q vsftpd; then
        echo "正在安装 vsftpd..."
        apt-get update
        apt-get install -y vsftpd
    fi

    # 创建FTP根目录
    mkdir -p "$FTP_BASE"

    # 备份原始配置文件
    if [ ! -f "$VSFTPD_CONF.original" ]; then
        cp "$VSFTPD_CONF" "$VSFTPD_CONF.original"
    fi

    # 使用cat和EOF覆盖写入正确的配置
    cat > "$VSFTPD_CONF" << 'EOF'
# 基本设置
listen=YES
anonymous_enable=NO
local_enable=YES
write_enable=YES
local_umask=022
dirmessage_enable=YES
xferlog_enable=YES
connect_from_port_20=YES

# 用户禁锢（隔离）核心配置 [2,3,6](@ref)
# 关键配置：将所有本地用户限制在其主目录内（实现用户间隔离）
chroot_local_user=YES
# 允许被禁锢的目录有写权限
allow_writeable_chroot=YES
# 启用一个例外列表，但结合chroot_local_user=YES，列表中的用户反而“不被”禁锢
chroot_list_enable=YES
chroot_list_file=/etc/vsftpd.chroot_list

# 被动模式设置
pasv_enable=YES
pasv_min_port=40000
pasv_max_port=50000

# 其他设置
secure_chroot_dir=/var/run/vsftpd/empty
pam_service_name=vsftpd
rsa_cert_file=/etc/ssl/certs/ssl-cert-snakeoil.pem
rsa_private_key_file=/etc/ssl/private/ssl-cert-snakeoil.key
ssl_enable=NO
EOF

    # 确保chroot列表文件存在
    touch "$CHROOT_LIST_FILE"

    # 配置防火墙规则 [2,8](@ref)
    echo "配置防火墙..."
    ufw allow 20/tcp > /dev/null 2>&1 || true
    ufw allow 21/tcp > /dev/null 2>&1 || true
    ufw allow 40000:50000/tcp > /dev/null 2>&1 || true

    # 重启vsftpd服务以使配置生效 [2,3](@ref)
    systemctl restart vsftpd
    echo "vsftpd 已安装并配置完成。"
}

# 创建共享用户组，并将work用户加入
create_shared_group() {
    if ! getent group "$SHARED_GROUP" > /dev/null; then
        groupadd "$SHARED_GROUP"
        echo "已创建共享用户组: $SHARED_GROUP"
    fi

    # 将work用户加入该组（如果work用户存在）
    if id "$WORK_USER" &>/dev/null; then
        usermod -aG "$SHARED_GROUP" "$WORK_USER"
        echo "已将用户 '$WORK_USER' 加入共享组 '$SHARED_GROUP'。"
    else
        echo "警告：系统内不存在用户 '$WORK_USER'，权限设置将不完整。"
    fi
}

# 创建FTP用户并设置随机密码
create_ftp_user() {
    local username=$1
    # 使用openssl生成随机密码
    local user_password=$(openssl rand -base64 "$PASSWORD_LENGTH")

    # 创建用户，将其家目录设置为FTP根目录下的子目录，并禁止其登录shell [2,5](@ref)
    useradd -m -d "$FTP_BASE/$username" -s /sbin/nologin -G "$SHARED_GROUP" "$username"
    echo "已创建系统用户: $username"

    # 设置密码
    echo "$username:$user_password" | chpasswd
    echo "已为用户 '$username' 设置随机密码。"

    # 返回生成的密码
    echo "$user_password"
}

# 设置FTP用户目录权限
setup_ftp_directory() {
    local username=$1
    local user_dir="$FTP_BASE/$username"

    # 确保目录存在
    mkdir -p "$user_dir"

    # 关键权限设置 [4](@ref)
    # 1. 目录所有者是新用户，所属组是共享组
    chown "$username:$SHARED_GROUP" "$user_dir"
    # 2. 设置目录权限：所有者有全部权限(7)，组用户有全部权限(7)，其他用户无权限(0)
    # 这使得新用户自己和同组（包括work）的成员都有读写执行的权限，而其他用户（包括其他FTP用户）无法访问。
    chmod 770 "$user_dir"

    echo "已设置目录权限: $user_dir (所有者: $username, 组: $SHARED_GROUP, 权限: 770)"
}

# 将用户添加到chroot例外列表（确保此用户不被禁锢）
# 注意：由于我们的配置是 chroot_local_user=YES，添加进这个列表的用户将“不受”禁锢，可以向上切换目录。
# 因此，我们不会将普通FTP用户添加进去，以确保他们被禁锢。work用户如果需要FTP登录并访问其他目录，则可以加入。
# 此脚本为演示，默认不将任何用户加入例外列表，确保所有新建用户都被隔离。
exempt_user_from_chroot() {
    local username=$1
    # 检查用户是否已在列表中
    if ! grep -q "^$username$" "$CHROOT_LIST_FILE"; then
        echo "$username" >> "$CHROOT_LIST_FILE"
        echo "注意：用户 '$username' 已被添加到chroot例外列表，将不受目录禁锢限制。"
    fi
}

# 打印创建结果
print_summary() {
    local username=$1
    local password=$2
    local user_dir="$FTP_BASE/$username"

    echo ""
    echo "================================================"
    echo "FTP 用户创建完成！"
    echo "================================================"
    echo "用户名: $username"
    echo "密码: $password"
    echo "用户目录: $user_dir"
    echo "共享用户组: $SHARED_GROUP"
    echo "具有特殊访问权限的用户: $WORK_USER"
    echo "------------------------------------------------"
    echo "用户隔离状态:"
    echo "  - 用户 '$username' 已被禁锢在自己的目录中，无法访问其他用户目录。"
    echo "  - 用户 '$WORK_USER' 对所有FTP用户目录拥有读写权限。"
    echo "------------------------------------------------"
    echo "FTP 连接信息:"
    echo "  主机: $(hostname -I | awk '{print $1}') 或您的服务器域名"
    echo "  端口: 21"
    echo "  协议: FTP (建议使用被动模式)"
    echo "================================================"
    echo "重要提示：请妥善保管以上密码信息。"
}

# -------------------------- 主程序 ---------------------------
main() {
    # 初始检查
    check_arguments "$@"
    check_root
    check_dependencies
    check_user_existence "$NEW_USER"

    # 执行流程
    # setup_vsftpd
    create_shared_group
    USER_PASSWORD=$(create_ftp_user "$NEW_USER")
    setup_ftp_directory "$NEW_USER"
    # 注意：此处特意不调用 exempt_user_from_chroot，确保新用户被禁锢。
    print_summary "$NEW_USER" "$USER_PASSWORD"
}

# 启动主程序
main "$@"