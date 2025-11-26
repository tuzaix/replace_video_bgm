from typing import Dict, Any, List

class SliceConfig:
    """直播切片全局配置与参数定义"""

    # 默认模型路径配置 (虽通过外部传参，但可在此定义推荐默认值或占位符)
    DEFAULT_MODEL_SIZE = "large-v3"
    
    # 场景化模式关键词与参数配置
    KEYWORDS_CONFIG: Dict[str, Dict[str, Any]] = {
        "ecommerce": {
            "high": [
                # 2024-2025 高频促单/逼单/福利词
                "价格", "多少钱", "上车", "链接", "库存", "没抢到", "炸", "福利", "送大家", "买一送一",
                "秒杀", "包邮", "到手价", "只有今天", "最后", "抢", "抢到就是赚到", "拼手速", "拼网速",
                "限量", "稀缺", "倒数", "开库存", "上链接", "炸福利", "别犹豫", "手慢无", "最后2分钟",
                "加急", "补货", "先付先得", "价格打下来", "破价", "机制", "天花板", "地板价", "王炸", "闭眼入",
                "不用想", "直接拍", "锁单", "真的是", "太划算",
            ],
            "mid": [
                # 信任构建与产品展示
                "大家", "集美", "兄弟", "姐妹们", "材质", "成分", "保证", "真的", "效果", "正品", "划算",
                "细节", "版型", "面料", "好评", "回购", "试穿", "上身", "展示", "颜色", "尺码", "关注",
                "粉丝团", "运费险", "放心", "质保", "七天无理由",
            ],
            "stop": [
                "下一个", "过", "next", "换一个", "准备好", "没抢到的姐妹", "吊牌价", "去上厕所", "休息一下",
                "喝口水", "下播", "明天见",
            ],
            "lookback": 2.0,       # 工业优化：增加回溯时间以保留完整语境
            "padding": 1.0,        # 工业优化：增加尾部填充防止截断
            "max_dur": 60,
            "pre_roll": 3.0,
            "post_roll": 5.0,
            "max_hard_limit": 60.0,
            "min_duration": 5.0,   # 工业优化：允许更短的强节奏片段
            "min_keyword_hits": 2,
            "max_cluster_gap": 60.0,
            "max_output_duration": 60.0,
            "min_output_duration": 10.0,
            "visual_keywords": ["product", "holding", "bottle", "box", "package", "hand", "screen"],
        },
        "game": {
            "high": [
                # 2024-2025 游戏高光/情绪/梗
                "卧槽", "牛逼", "666", "Nice", "救我", "别送", "一波", "赢了", "GG", "我C", "啊啊", "死了", "反杀",
                "操作", "秀", "大招", "广智救我", "红温", "急了", "破防", "硬控", "控制", "击飞", "沉默",
                "抢龙", "偷家", "What", "No way", "这波", "天秀", "丝血", "五杀", "Penta Kill", "Ace",
            ],
            "mid": [
                # 战术沟通与流行语
                "这是什么", "看来", "小心", "后面", "左边", "右边", "上上上", "撤撤撤", "集火",
                "抽象", "太抽象了", "搞抽象", "牢大", "复活", "幽默", "小丑", "还在嘴硬", "尽力了",
                "带不动", "坐牢", "折磨", "下饭", "喂饭", "厨师", "脚本", "外挂", "炸鱼", "演员",
                "城墙", "骗人的", "怎么玩", "拉扯", "配合", "撤退",
            ],
            "stop": [
                "这就尴尬了", "下一把", "排队", "休息一下", "下播", "明天见", "去吃饭", "上厕所",
            ],
            "lookback": 5.0,       # 工业优化：游戏高光通常爆发快，适度减少回溯，但保留足够前摇
            "padding": 3.0,
            "max_dur": 90,
            "pre_roll": 8.0,
            "post_roll": 5.0,
            "max_hard_limit": 60.0,
            "min_duration": 5.0,
            "min_keyword_hits": 1,
            "max_cluster_gap": 45.0,
            "max_output_duration": 60.0,
            "min_output_duration": 10.0,
            "visual_keywords": ["game", "interface", "character", "gun", "shooting", "health bar"],
        },
        "entertainment": {
            "high": [
                # 2024-2025 礼物感谢/互动高潮
                "谢谢", "感谢", "大哥", "点赞", "关注", "榜一", "送给", "爱你们", "比心", "礼物",
                "点关注", "不迷路", "粉丝灯牌", "亮灯牌", "卡牌子", "小心心", "大啤酒", "大墨镜",
                "跑车", "嘉年华", "火箭", "感谢大哥", "大哥大气", "老板大气", "守护", "么么哒", "家人",
                "欢迎大哥", "感谢关注",
            ],
            "mid": [
                # 互动维持与流行语
                "欢迎", "家人", "开心", "这首歌", "唱一个", "跳舞", "新进来的", "左上角", "福袋",
                "抢福袋", "分享直播间", "点点赞", "双击屏幕", "把赞点一点", "活跃一下", "听歌", "点歌",
                "才艺", "真心话", "连麦", "PK", "上票", "偷塔", "守塔", "别让", "水灵灵", "偷感",
                "包的", "那咋了",
            ],
            "stop": [
                "去上厕所", "喝口水", "下播", "明天见", "晚安", "休息", "去吃饭",
            ],
            "lookback": 2.0,
            "padding": 2.0,
            "max_dur": 60,
            "pre_roll": 5.0,
            "post_roll": 10.0,
            "max_hard_limit": 60.0,
            "min_duration": 10.0,
            "min_keyword_hits": 1,
            "max_cluster_gap": 60.0,
            "max_output_duration": 60.0,
            "min_output_duration": 10.0,
            "visual_keywords": ["stage", "sing", "dance", "instrument", "performance", "microphone"],
        },
    }

    # 表演模式默认参数
    PERFORMANCE_PARAMS = {
        "target_duration": 30,
        "min_silence_len": 1000,   # 工业优化：降低静音阈值以更灵敏检测停顿
        "silence_thresh": -40,     # dBFS
        "min_segment_sec": 5,      # 允许更短有效段
        "max_keep_sec": 60,
    }

    # 语音模式默认参数
    SPEECH_PARAMS = {
        "min_sec": 15,             # 略微降低下限
        "max_sec": 60,
        "language": "zh",
    }
    SUBTITLE_STYLE = {
        "font_name": "Microsoft YaHei",
        "font_size": 42,
        "primary_color": "#FFFFFF",
        "outline_color": "#000000",
        "back_color": "#000000",
        "outline": 2,
        "shadow": 0,
        "alignment": 2,
        "margin_v": 30,
        "encoding": 1,
        "highlight_color": "#FFE400",
    }
