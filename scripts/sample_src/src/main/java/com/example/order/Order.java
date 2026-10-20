package com.example.order;

public class Order {
    private Long id;
    private String orderNo;

    @NotBlank
    @Size(max = 20)
    private String receiverName;

    @NotBlank
    @Pattern(regexp = "^1[3-9]\\d{9}$")
    private String phone;

    @NotBlank
    @Size(max = 100)
    private String address;

    @NotBlank
    private java.math.BigDecimal amount;

    @Size(max = 200)
    private String remark;

    private String status;
}
