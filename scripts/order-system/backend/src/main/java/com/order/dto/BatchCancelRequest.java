package com.order.dto;

import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.Size;

import java.util.List;

/**
 * 批量取消请求。
 *
 * 单次最多 50 条；服务端会去重并逐条返回处理结果，允许部分成功。
 */
public class BatchCancelRequest {

    @NotEmpty(message = "请选择至少一个订单")
    @Size(max = 50, message = "单次最多取消50个订单")
    private List<Long> ids;

    public List<Long> getIds() {
        return ids;
    }

    public void setIds(List<Long> ids) {
        this.ids = ids;
    }
}
