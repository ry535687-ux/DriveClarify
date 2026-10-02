"""独立基础设施检查；不加载模型，不执行任何评测路线。"""
import datetime
import json
import sys
import torch


def main():
    result = dict(timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  scope='基础设施 CUDA 初始化与单元素计算，不属于模型/评测执行',
                  python=sys.executable,torch=torch.__version__,torch_cuda=torch.version.cuda)
    try:
        torch.cuda.init()
        x=torch.ones(1,device='cuda')
        torch.cuda.synchronize()
        result.update(pass_=x.item()==1.0,device=torch.cuda.get_device_name(0),
                      free_total_bytes=torch.cuda.mem_get_info())
    except Exception as exc:
        result.update(pass_=False,error=repr(exc))
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0 if result['pass_'] else 70


if __name__=='__main__':sys.exit(main())
