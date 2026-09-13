import board
import busio
import digitalio
import adafruit_ssd1305
from PIL import Image, ImageDraw, ImageFont

WIDTH = 128
HEIGHT = 32

i2c = busio.I2C(board.SCL, board.SDA)
reset_pin = digitalio.DigitalInOut(board.D4)

display = adafruit_ssd1305.SSD1305_I2C(
    WIDTH,
    HEIGHT,
    i2c,
    reset=reset_pin,
)

display.fill(0)
display.show()

image = Image.new("1", (WIDTH, HEIGHT))
draw = ImageDraw.Draw(image)
font = ImageFont.load_default()

draw.text((0, 0), "Pi 3 B+", font=font, fill=255)
draw.text((0, 12), "SSD1305 OLED", font=font, fill=255)

display.image(image)
display.show()
