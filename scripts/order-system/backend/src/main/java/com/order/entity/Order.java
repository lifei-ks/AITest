package com.order.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 订单实体。
 *
 * 字段上的校验注解会由 Spring 在 @Valid 时执行，失败消息直接返回给前端展示；
 * 这些中文消息同时也是自动化测试断言的可锚点文案。
 *
 * 注：本项目数据存内存，@Entity 仅用于标注领域实体结构。
 */
@Entity
@Table(name = "orders")
public class Order {

    @Id
    private Long id;

    @NotBlank(message = "订单号不能为空")
    private String orderNo;

    @NotBlank(message = "收货人不能为空")
    @Size(max = 20, message = "收货人不能超过20个字符")
    private String receiverName;

    @NotBlank(message = "手机号不能为空")
    @Pattern(regexp = "^1[3-9]\\d{9}$", message = "手机号格式不正确")
    private String phone;

    @NotBlank(message = "收货地址不能为空")
    @Size(max = 100, message = "收货地址不能超过100个字符")
    private String address;

    @NotNull(message = "订单金额不能为空")
    @DecimalMin(value = "0.01", message = "订单金额必须大于0")
    private BigDecimal amount;

    @Size(max = 200, message = "备注不能超过200个字符")
    private String remark;

    /** 新建订单默认是待支付，流转只能通过 Service 的 pay/ship/complete/cancel 修改。 */
    private OrderStatus status = OrderStatus.CREATED;

    private LocalDateTime createTime;

    private LocalDateTime updateTime;

    /** 供前端直接展示中文状态，避免前端再维护一份映射。 */
    public String getStatusLabel() {
        return status == null ? "" : status.getLabel();
    }

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public String getOrderNo() {
        return orderNo;
    }

    public void setOrderNo(String orderNo) {
        this.orderNo = orderNo;
    }

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

    public BigDecimal getAmount() {
        return amount;
    }

    public void setAmount(BigDecimal amount) {
        this.amount = amount;
    }

    public String getRemark() {
        return remark;
    }

    public void setRemark(String remark) {
        this.remark = remark;
    }

    public OrderStatus getStatus() {
        return status;
    }

    public void setStatus(OrderStatus status) {
        this.status = status;
    }

    public LocalDateTime getCreateTime() {
        return createTime;
    }

    public void setCreateTime(LocalDateTime createTime) {
        this.createTime = createTime;
    }

    public LocalDateTime getUpdateTime() {
        return updateTime;
    }

    public void setUpdateTime(LocalDateTime updateTime) {
        this.updateTime = updateTime;
    }
}
