package com.order.controller;

import com.order.common.ApiResponse;
import com.order.dto.CreateOrderRequest;
import com.order.dto.UpdateOrderRequest;
import com.order.entity.Order;
import com.order.entity.OrderStatus;
import com.order.service.OrderServiceImpl;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 订单接口。
 *
 * 9 个业务端点：创建 / 列表 / 详情 / 修改 / 删除 / 支付 / 发货 / 完成 / 取消。
 */
@RestController
@RequestMapping("/api/orders")
public class OrderController {

    private final OrderServiceImpl orderService;

    public OrderController(OrderServiceImpl orderService) {
        this.orderService = orderService;
    }

    @PostMapping
    public ApiResponse<Order> create(@Valid @RequestBody CreateOrderRequest request) {
        return ApiResponse.ok(orderService.create(request));
    }

    @GetMapping
    public ApiResponse<Map<String, Object>> list(
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "10") int pageSize,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) String keyword) {
        return ApiResponse.ok(orderService.list(page, pageSize, status, keyword));
    }

    /** 状态下拉选项，供前端筛选使用。 */
    @GetMapping("/status-options")
    public ApiResponse<List<Map<String, String>>> statusOptions() {
        List<Map<String, String>> options = new ArrayList<>();
        for (OrderStatus status : OrderStatus.values()) {
            Map<String, String> item = new LinkedHashMap<>();
            item.put("value", status.name());
            item.put("label", status.getLabel());
            options.add(item);
        }
        return ApiResponse.ok(options);
    }

    @GetMapping("/{id}")
    public ApiResponse<Order> detail(@PathVariable Long id) {
        return ApiResponse.ok(orderService.getById(id));
    }

    @PutMapping("/{id}")
    public ApiResponse<Order> update(@PathVariable Long id, @Valid @RequestBody UpdateOrderRequest request) {
        return ApiResponse.ok(orderService.update(id, request));
    }

    @DeleteMapping("/{id}")
    public ApiResponse<Void> delete(@PathVariable Long id) {
        orderService.delete(id);
        return ApiResponse.ok();
    }

    @PostMapping("/{id}/pay")
    public ApiResponse<Order> pay(@PathVariable Long id) {
        return ApiResponse.ok(orderService.pay(id));
    }

    @PostMapping("/{id}/ship")
    public ApiResponse<Order> ship(@PathVariable Long id) {
        return ApiResponse.ok(orderService.ship(id));
    }

    @PostMapping("/{id}/complete")
    public ApiResponse<Order> complete(@PathVariable Long id) {
        return ApiResponse.ok(orderService.complete(id));
    }

    @PostMapping("/{id}/cancel")
    public ApiResponse<Order> cancel(@PathVariable Long id) {
        return ApiResponse.ok(orderService.cancel(id));
    }

    @PostMapping("/{id}/refund")
    public ApiResponse<Order> refund(@PathVariable Long id) {
        return ApiResponse.ok(orderService.refund(id));
    }
}
