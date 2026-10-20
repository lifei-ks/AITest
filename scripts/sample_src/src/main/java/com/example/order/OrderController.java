package com.example.order;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/orders")
public class OrderController {

    @PostMapping("/create")
    public void create() {
    }

    @PostMapping("/{id}/pay")
    public void pay(@PathVariable String id) {
    }

    @PostMapping("/{id}/ship")
    public void ship(@PathVariable String id) {
    }

    @PostMapping("/{id}/complete")
    public void complete(@PathVariable String id) {
    }

    @PostMapping("/{id}/cancel")
    public void cancel(@PathVariable String id) {
    }

    @GetMapping("/list")
    public void list() {
    }

    @GetMapping("/{id}")
    public void detail(@PathVariable String id) {
    }
}
