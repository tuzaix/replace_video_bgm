"""
Subtitle Style Configuration
Presets inspired by CapCut (剪映) and other popular short video trends.
"""

def get_subtitle_styles(font_family: str = "Microsoft YaHei"):
    """Returns a dictionary of subtitle styles with the specified font family."""
    return {
        "classic_white": f"""
            .line {{
                font-size: 20px;
                font-family: '{font_family}', sans-serif;
                color: white;
                text-shadow: 2px 2px 4px rgba(0, 0, 0, 0.8);
                background: rgba(0, 0, 0, 0.5);
                padding: 12px 20px;
                border-radius: 10px;
                margin: 20px;
                text-align: center;
            }}
        """,
        "tiktok_yellow": f"""
            .line {{
                font-size: 55px;
                font-family: '{font_family}', sans-serif;
                font-weight: bold;
                color: #ffff00;
                text-shadow: 3px 3px 0px #000, -1px -1px 0px #000, 1px -1px 0px #000, -1px 1px 0px #000, 1px 1px 0px #000;
                padding: 10px;
                margin: 30px;
                text-align: center;
            }}
        """,
        "vlog_minimal": f"""
            .line {{
                font-size: 34px;
                font-family: '{font_family}', sans-serif;
                color: #ffffff;
                text-shadow: 1px 1px 2px rgba(0, 0, 0, 0.5);
                letter-spacing: 2px;
                margin-bottom: 60px;
                text-align: center;
            }}
        """,
        "jianyin_bubble": f"""
            .line {{
                font-size: 42px;
                font-family: '{font_family}', sans-serif;
                color: #333333;
                background: #ffffff;
                border: 4px solid #333333;
                padding: 10px 25px;
                border-radius: 15px;
                margin: 20px;
                text-align: center;
                box-shadow: 5px 5px 0px #333333;
            }}
        """,
        "jianyin_black_gold": f"""
            .line {{
                font-size: 48px;
                font-family: '{font_family}', sans-serif;
                font-weight: bold;
                color: #FFD700;
                background: #1A1A1A;
                padding: 15px 30px;
                border: 2px solid #FFD700;
                text-align: center;
                text-transform: uppercase;
            }}
        """,
        "news_headline": f"""
            .line {{
                font-size: 45px;
                font-family: '{font_family}', sans-serif;
                font-weight: 900;
                color: white;
                background: #E31212;
                padding: 8px 40px;
                text-align: center;
                box-shadow: 0 10px 0 #8B0000;
            }}
        """,
        "cute_pink": f"""
            .line {{
                font-size: 44px;
                font-family: '{font_family}', sans-serif;
                color: #FF69B4;
                background: rgba(255, 255, 255, 0.9);
                border: 3px dashed #FF69B4;
                padding: 12px 24px;
                border-radius: 30px;
                text-align: center;
            }}
        """,
        "cyberpunk": f"""
            .line {{
                font-size: 50px;
                font-family: '{font_family}', sans-serif;
                font-weight: bold;
                color: #00FFFF;
                text-shadow: 3px 3px 0px #FF00FF, -2px -2px 0px #000;
                background: rgba(0, 0, 0, 0.8);
                padding: 10px 20px;
                border-left: 10px solid #FF00FF;
                text-align: center;
            }}
        """
    }
