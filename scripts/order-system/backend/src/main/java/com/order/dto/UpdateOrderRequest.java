package com.order.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

/**
 * 修改订单请求。只允许修改收货信息与备注，不允许直接改状态
 * （状态必须走 pay/ship/complete/cancel 四个动作）。
 */
public class UpdateOrderRequest {

    @NotBlank(message = "收货人不能为空")
    @Size(max = 20, message = "收货人不能超过20个字符")
    private String receiverName;

    @NotBlank(message = "手机号不能为空")
    @Pattern(regexp = "^1[3-9]\\d{9}$", message = "手机号格式不正确")
    private String phone;

    @NotBlank(message = "收货地址不能为空")
    @Size(max = 100, message = "收货地址不能超过100个字符")
    private String address;

    @Size(max = 200, message = "备注不能超过200个字符")
    private String remark;

    public String getReceiverName() {
        return receiverName;
    }

    public void setReceiverName(String receiverName) {
        this.receiverName = receiverName;
    }

    public String getPhone() {
        return phone;
    }

    public void setPhone(String phone) {
        this.phone = phone;
    }

    public String getAddress() {
        return address;
    }

    public void setAddress(String address) {
        this.address = address;
    }

    public String getRemark() {
        return remark;
    }

    public void setRemark(String remark) {
        this.remark = remark;
    }
}
