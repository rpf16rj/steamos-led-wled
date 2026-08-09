// SPDX-License-Identifier: GPL-2.0+

#include <linux/device.h>
#include <linux/fs.h>
#include <linux/kstrtox.h>
#include <linux/led-class-multicolor.h>
#include <linux/miscdevice.h>
#include <linux/mutex.h>
#include <linux/platform_device.h>
#include <linux/poll.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/stringify.h>
#include <linux/sysfs.h>
#include <linux/timekeeping.h>
#include <linux/uaccess.h>
#include <linux/wait.h>

#define DRVNAME "valve-leds-shim"
#define LED_CLASS_NAME "valve-leds"

#define VALVE_NUM_LEDS 17
#define VALVE_NUM_COMPONENTS 3
#define VALVE_BRIGHTNESS_DEFAULT 255
#define VALVE_BRIGHTNESS_HALF 56
#define VALVE_BRIGHTNESS_MAX 255
#define VALVE_DELAY_RANGE_MIN 0
#define VALVE_DELAY_RANGE_MAX 20
#define VALVE_DELAY_DEFAULT 8
#define VALVE_BREATH_OFFSET_DEFAULT 4
#define VALVE_BREATH_LEVEL_DEFAULT 32
#define VALVE_PATROL_NUM_DEFAULT 3
#define VALVE_COLOR_SHIFT_DEFAULT 5

#define VALVE_LEDS_UAPI_MAGIC 0x564c4544 /* "VLED" */
#define VALVE_LEDS_UAPI_VERSION 1

enum valve_leds_effect {
	VALVE_LEDS_EFFECT_OFF = 0,
	VALVE_LEDS_EFFECT_MANUAL,
	VALVE_LEDS_EFFECT_NORMAL,
	VALVE_LEDS_EFFECT_RAINBOW,
	VALVE_LEDS_EFFECT_BREATH,
	VALVE_LEDS_EFFECT_PATROL,
	VALVE_LEDS_EFFECT_FACTORY,
	VALVE_LEDS_EFFECT_DEMO,
};

static bool debug_log;
module_param(debug_log, bool, 0644);
MODULE_PARM_DESC(debug_log, "Print writes to the kernel log");

struct valve_leds_pixel {
	u8 r;
	u8 g;
	u8 b;
	u8 brightness;
};

struct valve_leds_snapshot {
	u32 magic;
	u16 version;
	u16 size;

	u64 seq;
	u64 monotonic_ns;

	u8 enabled;
	u8 effect;
	u8 brightness_scale;
	u8 delay;

	u8 breath_offset;
	u8 breath_level;
	u8 patrol_num;
	u8 color_shift;

	struct valve_leds_pixel pixels[VALVE_NUM_LEDS];
} __packed;

struct valve_leds {
	struct platform_device *pdev;
	struct miscdevice miscdev;
	struct mutex lock;
	wait_queue_head_t waitq;
	u64 seq;
	u64 monotonic_ns;

	struct valve_led {
		struct led_classdev_mc mcdev;
		struct mc_subled rgb[VALVE_NUM_COMPONENTS];
		int index;
		u8 brightness;
	} leds[VALVE_NUM_LEDS];

	bool enabled;
	u8 effect_index;
	u8 delay;
	u8 breath_offset;
	u8 breath_level;
	u8 patrol_num;
	u8 color_shift;
	u8 brightness_scale;
	u8 brightness_startup;
	u8 multi_intensity_startup[VALVE_NUM_COMPONENTS];
};

static struct platform_device *pdev;
static struct valve_leds *active_leds;

static const char *const effect_names[] = {
	[VALVE_LEDS_EFFECT_OFF] = "off",
	[VALVE_LEDS_EFFECT_MANUAL] = "manual",
	[VALVE_LEDS_EFFECT_NORMAL] = "normal",
	[VALVE_LEDS_EFFECT_RAINBOW] = "rainbow",
	[VALVE_LEDS_EFFECT_BREATH] = "breath",
	[VALVE_LEDS_EFFECT_PATROL] = "patrol",
	[VALVE_LEDS_EFFECT_FACTORY] = "factory",
	[VALVE_LEDS_EFFECT_DEMO] = "demo",
};

static void valve_leds_state_changed(struct valve_leds *leds)
{
	leds->seq++;
	leds->monotonic_ns = ktime_get_ns();
	wake_up_interruptible(&leds->waitq);
}

static void valve_leds_log_global(struct valve_leds *leds, const char *name, const char *value)
{
	if (!debug_log)
		return;

	dev_info(&leds->pdev->dev, "all %s=%s\n", name, value);
}

static void valve_leds_log_global_u8(struct valve_leds *leds, const char *name, unsigned int value)
{
	if (!debug_log)
		return;

	dev_info(&leds->pdev->dev, "all %s=%u\n", name, value);
}

static ssize_t enabled_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct valve_leds *leds = dev_get_drvdata(dev->parent);
	bool enabled;

	mutex_lock(&leds->lock);
	enabled = leds->enabled;
	mutex_unlock(&leds->lock);

	return sysfs_emit(buf, "%d\n", enabled);
}

static ssize_t enabled_store(struct device *dev, struct device_attribute *attr,
			     const char *buf, size_t count)
{
	struct valve_leds *leds = dev_get_drvdata(dev->parent);
	bool enabled;
	int ret;

	ret = kstrtobool(buf, &enabled);
	if (ret < 0)
		return ret;

	mutex_lock(&leds->lock);
	leds->enabled = enabled;
	valve_leds_log_global(leds, "enabled", enabled ? "1" : "0");
	valve_leds_state_changed(leds);
	mutex_unlock(&leds->lock);

	return count;
}

static ssize_t effect_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct valve_leds *leds = dev_get_drvdata(dev->parent);
	u8 effect_index;

	mutex_lock(&leds->lock);
	effect_index = leds->effect_index;
	mutex_unlock(&leds->lock);

	if (effect_index >= ARRAY_SIZE(effect_names))
		return -EINVAL;

	return sysfs_emit(buf, "%s\n", effect_names[effect_index]);
}

static ssize_t effect_store(struct device *dev, struct device_attribute *attr,
			    const char *buf, size_t count)
{
	struct valve_leds *leds = dev_get_drvdata(dev->parent);
	int effect_index;

	effect_index = __sysfs_match_string(effect_names, ARRAY_SIZE(effect_names), buf);
	if (effect_index < 0)
		return effect_index;

	mutex_lock(&leds->lock);
	leds->effect_index = effect_index;
	valve_leds_log_global(leds, "effect", effect_names[effect_index]);
	valve_leds_state_changed(leds);
	mutex_unlock(&leds->lock);

	return count;
}

static ssize_t effect_index_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	ssize_t len = 0;
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(effect_names); i++) {
		if (i > 0)
			len += sysfs_emit_at(buf, len, " ");
		len += sysfs_emit_at(buf, len, "%s", effect_names[i]);
	}
	len += sysfs_emit_at(buf, len, "\n");

	return len;
}

static ssize_t delay_range_show(struct device *dev, struct device_attribute *attr,
				char *buf)
{
	return sysfs_emit(buf, "%d-%d\n", VALVE_DELAY_RANGE_MIN,
			  VALVE_DELAY_RANGE_MAX);
}

static ssize_t brightness_startup_show(struct device *dev, struct device_attribute *attr,
				       char *buf)
{
	struct valve_leds *leds = dev_get_drvdata(dev->parent);
	u8 brightness_startup;

	mutex_lock(&leds->lock);
	brightness_startup = leds->brightness_startup;
	mutex_unlock(&leds->lock);

	return sysfs_emit(buf, "0x%02x\n", brightness_startup);
}

static ssize_t brightness_startup_store(struct device *dev, struct device_attribute *attr,
					const char *buf, size_t count)
{
	struct valve_leds *leds = dev_get_drvdata(dev->parent);
	unsigned int val;
	int ret;

	ret = kstrtouint(buf, 0, &val);
	if (ret)
		return ret;
	if (val > VALVE_BRIGHTNESS_MAX)
		return -EINVAL;

	mutex_lock(&leds->lock);
	leds->brightness_startup = val;
	valve_leds_log_global_u8(leds, "brightness_startup", val);
	mutex_unlock(&leds->lock);

	return count;
}

static ssize_t multi_intensity_startup_show(struct device *dev, struct device_attribute *attr,
					    char *buf)
{
	struct valve_leds *leds = dev_get_drvdata(dev->parent);
	u8 rgb[VALVE_NUM_COMPONENTS];

	mutex_lock(&leds->lock);
	memcpy(rgb, leds->multi_intensity_startup, sizeof(rgb));
	mutex_unlock(&leds->lock);

	return sysfs_emit(buf, "%hhu %hhu %hhu\n", rgb[0], rgb[1], rgb[2]);
}

static ssize_t multi_intensity_startup_store(struct device *dev, struct device_attribute *attr,
					     const char *buf, size_t count)
{
	struct valve_leds *leds = dev_get_drvdata(dev->parent);
	u8 rgb[VALVE_NUM_COMPONENTS];
	int ret;

	ret = sscanf(buf, "%hhu %hhu %hhu", &rgb[0], &rgb[1], &rgb[2]);
	if (ret != VALVE_NUM_COMPONENTS)
		return -EINVAL;

	mutex_lock(&leds->lock);
	memcpy(leds->multi_intensity_startup, rgb, sizeof(rgb));
	if (debug_log)
		dev_info(&leds->pdev->dev, "all multi_intensity_startup=%u,%u,%u\n",
			 rgb[0], rgb[1], rgb[2]);
	mutex_unlock(&leds->lock);

	return count;
}

static DEVICE_ATTR_RW(enabled);
static DEVICE_ATTR_RW(effect);
static DEVICE_ATTR_RO(effect_index);
static DEVICE_ATTR_RO(delay_range);
static DEVICE_ATTR_RW(brightness_startup);
static DEVICE_ATTR_RW(multi_intensity_startup);

struct valve_leds_int_attr {
	struct device_attribute attr;
	const char *name;
	size_t offset;
	unsigned int max;
};

static ssize_t int_attr_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	struct valve_leds *leds = dev_get_drvdata(dev->parent);
	struct valve_leds_int_attr *vattr = container_of(attr, typeof(*vattr), attr);
	u8 *value = (u8 *)leds + vattr->offset;
	u8 snapshot;

	mutex_lock(&leds->lock);
	snapshot = *value;
	mutex_unlock(&leds->lock);

	return sysfs_emit(buf, "0x%02x\n", snapshot);
}

static ssize_t int_attr_store(struct device *dev, struct device_attribute *attr,
			      const char *buf, size_t size)
{
	struct valve_leds *leds = dev_get_drvdata(dev->parent);
	struct valve_leds_int_attr *vattr = container_of(attr, typeof(*vattr), attr);
	u8 *value = (u8 *)leds + vattr->offset;
	unsigned int val;
	int ret;

	ret = kstrtouint(buf, 0, &val);
	if (ret)
		return ret;
	if (val > vattr->max)
		return -EINVAL;

	mutex_lock(&leds->lock);
	*value = (u8)val;
	valve_leds_log_global_u8(leds, vattr->name, val);
	valve_leds_state_changed(leds);
	mutex_unlock(&leds->lock);

	return size;
}

#define VALVE_LEDS_INT_ATTR(_name, _max) \
	static struct valve_leds_int_attr dev_attr_##_name = { \
		.attr = __ATTR(_name, 0644, int_attr_show, int_attr_store), \
		.name = __stringify(_name), \
		.offset = offsetof(struct valve_leds, _name), \
		.max = (_max), \
	}

VALVE_LEDS_INT_ATTR(delay, VALVE_DELAY_RANGE_MAX);
VALVE_LEDS_INT_ATTR(breath_offset, U8_MAX);
VALVE_LEDS_INT_ATTR(breath_level, U8_MAX);
VALVE_LEDS_INT_ATTR(patrol_num, U8_MAX);
VALVE_LEDS_INT_ATTR(color_shift, U8_MAX);
VALVE_LEDS_INT_ATTR(brightness_scale, U8_MAX);

static struct attribute *valve_leds_attrs[] = {
	&dev_attr_effect.attr,
	&dev_attr_effect_index.attr,
	&dev_attr_enabled.attr,
	&dev_attr_delay.attr.attr,
	&dev_attr_delay_range.attr,
	&dev_attr_breath_offset.attr.attr,
	&dev_attr_breath_level.attr.attr,
	&dev_attr_patrol_num.attr.attr,
	&dev_attr_color_shift.attr.attr,
	&dev_attr_brightness_scale.attr.attr,
	&dev_attr_brightness_startup.attr,
	&dev_attr_multi_intensity_startup.attr,
	NULL,
};

static const struct attribute_group valve_leds_attr_group = {
	.attrs = valve_leds_attrs,
};

struct valve_leds_file {
	struct valve_leds *leds;
	u64 last_seen_seq;
};

static void valve_leds_fill_snapshot(struct valve_leds *leds, struct valve_leds_snapshot *snapshot)
{
	int i;

	memset(snapshot, 0, sizeof(*snapshot));
	snapshot->magic = VALVE_LEDS_UAPI_MAGIC;
	snapshot->version = VALVE_LEDS_UAPI_VERSION;
	snapshot->size = sizeof(*snapshot);
	snapshot->seq = leds->seq;
	snapshot->monotonic_ns = leds->monotonic_ns;
	snapshot->enabled = leds->enabled;
	snapshot->effect = leds->effect_index;
	snapshot->brightness_scale = leds->brightness_scale;
	snapshot->delay = leds->delay;
	snapshot->breath_offset = leds->breath_offset;
	snapshot->breath_level = leds->breath_level;
	snapshot->patrol_num = leds->patrol_num;
	snapshot->color_shift = leds->color_shift;

	for (i = 0; i < VALVE_NUM_LEDS; i++) {
		snapshot->pixels[i].r = leds->leds[i].rgb[0].intensity;
		snapshot->pixels[i].g = leds->leds[i].rgb[1].intensity;
		snapshot->pixels[i].b = leds->leds[i].rgb[2].intensity;
		snapshot->pixels[i].brightness = leds->leds[i].brightness;
	}
}

static int valve_leds_state_open(struct inode *inode, struct file *file)
{
	struct valve_leds_file *ctx;

	if (!active_leds)
		return -ENODEV;

	ctx = kzalloc(sizeof(*ctx), GFP_KERNEL);
	if (!ctx)
		return -ENOMEM;

	ctx->leds = active_leds;
	file->private_data = ctx;

	return 0;
}

static int valve_leds_state_release(struct inode *inode, struct file *file)
{
	kfree(file->private_data);
	return 0;
}

static ssize_t valve_leds_state_read(struct file *file, char __user *buf, size_t count,
				     loff_t *ppos)
{
	struct valve_leds_file *ctx = file->private_data;
	struct valve_leds_snapshot snapshot;

	if (!ctx || !ctx->leds)
		return -ENODEV;
	if (count < sizeof(snapshot))
		return -EINVAL;

	mutex_lock(&ctx->leds->lock);
	valve_leds_fill_snapshot(ctx->leds, &snapshot);
	ctx->last_seen_seq = snapshot.seq;
	mutex_unlock(&ctx->leds->lock);

	if (copy_to_user(buf, &snapshot, sizeof(snapshot)))
		return -EFAULT;

	return sizeof(snapshot);
}

static __poll_t valve_leds_state_poll(struct file *file, poll_table *wait)
{
	struct valve_leds_file *ctx = file->private_data;
	__poll_t mask = 0;
	u64 seq;

	if (!ctx || !ctx->leds)
		return EPOLLERR;

	poll_wait(file, &ctx->leds->waitq, wait);

	mutex_lock(&ctx->leds->lock);
	seq = ctx->leds->seq;
	mutex_unlock(&ctx->leds->lock);

	if (seq != ctx->last_seen_seq)
		mask |= EPOLLIN | EPOLLRDNORM;

	return mask;
}

static const struct file_operations valve_leds_state_fops = {
	.owner = THIS_MODULE,
	.open = valve_leds_state_open,
	.release = valve_leds_state_release,
	.read = valve_leds_state_read,
	.poll = valve_leds_state_poll,
	.llseek = noop_llseek,
};

static int valve_leds_set_brightness(struct led_classdev *led_cdev,
				     enum led_brightness brightness)
{
	struct valve_leds *leds = dev_get_drvdata(led_cdev->dev->parent);
	struct led_classdev_mc *mcdev = lcdev_to_mccdev(led_cdev);
	struct valve_led *led = container_of(mcdev, typeof(*led), mcdev);

	mutex_lock(&leds->lock);
	led_mc_calc_color_components(mcdev, brightness);
	led->brightness = brightness;

	if (debug_log)
		dev_info(&leds->pdev->dev, "led[%d] brightness=%u color=%u,%u,%u\n",
			 led->index, brightness, led->rgb[0].intensity,
			 led->rgb[1].intensity, led->rgb[2].intensity);

	valve_leds_state_changed(leds);
	mutex_unlock(&leds->lock);

	return 0;
}

static int valve_leds_probe(struct platform_device *pdev)
{
	struct valve_leds *vleds;
	int i, c;
	int ret;

	vleds = devm_kzalloc(&pdev->dev, sizeof(*vleds), GFP_KERNEL);
	if (!vleds)
		return -ENOMEM;

	mutex_init(&vleds->lock);
	init_waitqueue_head(&vleds->waitq);
	vleds->pdev = pdev;
	vleds->seq = 1;
	vleds->monotonic_ns = ktime_get_ns();
	vleds->enabled = true;
	vleds->effect_index = VALVE_LEDS_EFFECT_OFF;
	vleds->delay = VALVE_DELAY_DEFAULT;
	vleds->brightness_scale = VALVE_BRIGHTNESS_HALF;
	vleds->brightness_startup = VALVE_BRIGHTNESS_HALF;
	vleds->breath_offset = VALVE_BREATH_OFFSET_DEFAULT;
	vleds->breath_level = VALVE_BREATH_LEVEL_DEFAULT;
	vleds->patrol_num = VALVE_PATROL_NUM_DEFAULT;
	vleds->color_shift = VALVE_COLOR_SHIFT_DEFAULT;
	vleds->multi_intensity_startup[2] = VALVE_BRIGHTNESS_MAX;

	platform_set_drvdata(pdev, vleds);

	for (i = 0; i < VALVE_NUM_LEDS; i++) {
		for (c = 0; c < VALVE_NUM_COMPONENTS; c++) {
			vleds->leds[i].rgb[c].color_index = LED_COLOR_ID_RED + c;
			vleds->leds[i].rgb[c].brightness = VALVE_BRIGHTNESS_DEFAULT;
			vleds->leds[i].rgb[c].channel = c;
			vleds->leds[i].rgb[c].intensity = 0;
		}

		vleds->leds[i].index = i;
		vleds->leds[i].brightness = VALVE_BRIGHTNESS_DEFAULT;
		vleds->leds[i].mcdev.led_cdev.name = devm_kasprintf(&pdev->dev, GFP_KERNEL,
								    "%s[%d]", LED_CLASS_NAME, i);
		if (!vleds->leds[i].mcdev.led_cdev.name)
			return -ENOMEM;

		vleds->leds[i].mcdev.led_cdev.brightness = VALVE_BRIGHTNESS_DEFAULT;
		vleds->leds[i].mcdev.led_cdev.max_brightness = VALVE_BRIGHTNESS_MAX;
		vleds->leds[i].mcdev.led_cdev.brightness_set_blocking = valve_leds_set_brightness;
		vleds->leds[i].mcdev.num_colors = VALVE_NUM_COMPONENTS;
		vleds->leds[i].mcdev.subled_info = vleds->leds[i].rgb;

		ret = devm_led_classdev_multicolor_register(&pdev->dev,
							    &vleds->leds[i].mcdev);
		if (ret)
			return ret;

		ret = devm_device_add_group(vleds->leds[i].mcdev.led_cdev.dev,
					    &valve_leds_attr_group);
		if (ret)
			return ret;
	}

	vleds->miscdev.minor = MISC_DYNAMIC_MINOR;
	vleds->miscdev.name = DRVNAME;
	vleds->miscdev.fops = &valve_leds_state_fops;
	vleds->miscdev.parent = &pdev->dev;
	vleds->miscdev.mode = 0444;

	active_leds = vleds;

	ret = misc_register(&vleds->miscdev);
	if (ret) {
		active_leds = NULL;
		dev_err(&pdev->dev, "failed to register /dev/%s: %d\n",
			DRVNAME, ret);
		return ret;
	}

	dev_info(&pdev->dev, "registered fake Steam front light bar interface\n");

	return 0;
}

static void valve_leds_remove(struct platform_device *pdev)
{
	struct valve_leds *vleds = platform_get_drvdata(pdev);

	active_leds = NULL;
	misc_deregister(&vleds->miscdev);
}

static struct platform_driver valve_leds_driver = {
	.probe = valve_leds_probe,
	.remove = valve_leds_remove,
	.driver = {
		.name = DRVNAME,
	},
};

static int __init valve_leds_init(void)
{
	int ret;

	ret = platform_driver_register(&valve_leds_driver);
	if (ret < 0) {
		pr_err("%s(): failed to register driver: %d\n", __func__, ret);
		return ret;
	}

	pdev = platform_device_register_simple(DRVNAME, -1, NULL, 0);
	if (IS_ERR(pdev)) {
		pr_err("%s(): failed to register device: %ld\n", __func__, PTR_ERR(pdev));
		platform_driver_unregister(&valve_leds_driver);
		return PTR_ERR(pdev);
	}

	return 0;
}

static void __exit valve_leds_exit(void)
{
	platform_device_unregister(pdev);
	platform_driver_unregister(&valve_leds_driver);
}

module_init(valve_leds_init);
module_exit(valve_leds_exit);

MODULE_AUTHOR("Valve Corporation");
MODULE_AUTHOR("Anna Oake");
MODULE_DESCRIPTION("Virtual front bar LED shim for your Steam Machine-like computer");
MODULE_LICENSE("GPL");

// q[x<62!!!!!!!!!!
