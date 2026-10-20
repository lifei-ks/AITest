package com.order.controller;

import com.order.common.ApiResponse;
import com.order.repository.OrderRepository;
import com.order.service.OrderServiceImpl;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * 演示辅助接口。
 *
 * 仅供自动化测试重置被测数据使用：把订单库恢复成 4 条种子数据，
 * 保证每次执行都能从确定的初始状态开始。
 */
@RestController
@RequestMapping("/api/demo")
public class DemoController {

    private final OrderServiceImpl orderService;
    private final OrderRepository orderRepository;

    public DemoController(OrderServiceImpl orderService, OrderRepository orderRepository) {
        this.orderService = orderService;
        this.orderRepository = orderRepository;
    }

    @PostMapping("/reset")
    public ApiResponse<Map<String, Object>> reset() {
        orderService.reset();
        return ApiResponse.ok(Map.of("count", orderRepository.count()));
    }
}
