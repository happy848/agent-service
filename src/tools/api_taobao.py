# https://cnfans.com/product?id=655280629872&platform=TAOBAO

from client.taobao_client import taobao_client

# 创建一个api_taobao对象来导出函数
class ApiTaobao:
    """淘宝API工具类"""
    
    @staticmethod
    async def get_product_info(product_id: str, platform: str = "TAOBAO"):
        """获取商品信息"""
        return await taobao_client.get_product_info_with_api(product_id)


# 导出api_taobao实例
api_taobao = ApiTaobao()
