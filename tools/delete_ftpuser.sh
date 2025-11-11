#!/bin/bash
# 脚本：delete_ftp_user.sh
# 功能：安全删除FTP用户，并清理相关目录和配置
# 用法：sudo ./delete_ftp_user.sh <要删除的用户名>

set -e  # 遇到错误立即退出

# -------------------------- 变量定义 ---------------------------
# 定义FTP根目录（应与创建脚本保持一致）
FTP_BASE="/home"
# FTP配置文件目录
FTP_CONFIG_DIR="/etc/vsftpd"
# 要删除的用户名（从参数获取）
USERNAME="$1"

# -------------------------- 函数定义 ---------------------------

# 检查root权限
check_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "错误：此脚本必须以 root 权限运行。"
        exit 1
    fi
}

# 检查参数
check_arguments() {
    if [ $# -ne 1 ]; then
        echo "用法: $0 <要删除的用户名>"
        exit 1
    fi
}

# 检查用户是否存在
check_user_exists() {
    if ! id "$1" &>/dev/null; then
        echo "错误：用户 '$1' 不存在。"
        exit 1
    fi
}

# 删除用户相关的进程
kill_user_processes() {
    echo "检查并终止用户进程..."
    # 查找并终止该用户的进程
    if pgrep -u "$USERNAME" > /dev/null; then
        echo "终止用户 $USERNAME 的进程..."
        pkill -9 -u "$USERNAME" || true
        # 等待一段时间让进程完全终止
        sleep 2
    fi
}

# 删除系统用户和目录
delete_system_user() {
    echo "删除系统用户和主目录..."
    # 使用userdel命令删除用户及其主目录
    if userdel -r "$USERNAME" 2>/dev/null; then
        echo "已删除系统用户: $USERNAME"
    else
        echo "警告：使用userdel删除用户失败，尝试手动清理..."
        # 手动删除用户条目
        sed -i "/^$USERNAME:/d" /etc/passwd
        sed -i "/^$USERNAME:/d" /etc/shadow
        # 删除用户组（如果为空）
        groupdel "$USERNAME" 2>/dev/null || true
    fi
}

# 清理FTP相关目录
cleanup_ftp_directories() {
    local user_dir="$FTP_BASE/$USERNAME"
    
    echo "清理FTP目录..."
    if [ -d "$user_dir" ]; then
        # 备份重要数据（可选）
        # echo "备份用户数据..."
        # tar -czf "/tmp/${USERNAME}_backup_$(date +%Y%m%d).tar.gz" "$user_dir" 2>/dev/null || true
        
        # 删除用户目录
        rm -rf "$user_dir"
        echo "已删除FTP目录: $user_dir"
    fi
}

# 清理FTP配置文件
cleanup_ftp_config() {
    echo "清理FTP配置文件..."
    
    # 从chroot列表中移除用户
    if [ -f "$FTP_CONFIG_DIR/chroot_list" ]; then
        if grep -q "^$USERNAME$" "$FTP_CONFIG_DIR/chroot_list"; then
            sed -i "/^$USERNAME$/d" "$FTP_CONFIG_DIR/chroot_list"
            echo "已从chroot列表移除用户。"
        fi
    fi
    
    # 从其他配置文件中移除用户（根据您的配置调整）
    local user_conf_file="$FTP_CONFIG_DIR/vsftpd_user_conf/$USERNAME"
    if [ -f "$user_conf_file" ]; then
        rm -f "$user_conf_file"
        echo "已删除用户配置文件: $user_conf_file"
    fi
}

# 重启FTP服务
restart_ftp_service() {
    echo "重启FTP服务..."
    if systemctl is-active --quiet vsftpd; then
        systemctl restart vsftpd
        echo "FTP服务已重启。"
    else
        echo "FTP服务未运行，无需重启。"
    fi
}

# 验证删除结果
verify_deletion() {
    echo "验证删除结果..."
    if ! id "$USERNAME" &>/dev/null; then
        if [ ! -d "$FTP_BASE/$USERNAME" ]; then
            echo "✓ 用户 $USERNAME 已成功删除。"
        else
            echo "⚠ 用户账户已删除，但目录清理可能需要手动检查。"
        fi
    else
        echo "✗ 用户删除可能不完整，请手动检查。"
    fi
}

# 记录操作日志
log_deletion() {
    local log_file="/var/log/ftp_user_management.log"
    echo "$(date '+%Y-%m-%d %H:%M:%S') - 删除FTP用户: $USERNAME" >> "$log_file"
}

# -------------------------- 主程序 ---------------------------
main() {
    echo "开始删除FTP用户: $USERNAME"
    echo "================================="
    
    # 初始检查
    check_arguments "$@"
    check_root
    check_user_exists "$USERNAME"
    
    # 确认操作
    read -p "确定要删除FTP用户 '$USERNAME' 吗？此操作不可逆！(y/N): " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo "操作已取消。"
        exit 0
    fi
    
    # 执行删除流程
    kill_user_processes
    cleanup_ftp_directories
    delete_system_user
    cleanup_ftp_config
    restart_ftp_service
    verify_deletion
    log_deletion
    
    echo "================================="
    echo "FTP用户删除流程完成。"
}

# 启动主程序
main "$@"