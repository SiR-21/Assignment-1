package exp01;

import java.io.BufferedWriter;
import java.io.IOException;
import java.lang.management.GarbageCollectorMXBean;
import java.lang.management.ManagementFactory;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.Properties;
import java.util.Random;
import java.util.SplittableRandom;

/**
 * Dependency-free, blocked experiment for the three unchanged assignment sorters.
 * A fresh JVM is one replicate. Every measured block contains all 90 conditions in
 * a reproducibly shuffled order. Repetitions within a timed batch share the same
 * input keys but have separate arrays; batch repetitions are not independent data.
 */
public final class Benchmark {
    private static final int[] SIZES = {128, 512, 2048};
    private static final String[] DISTRIBUTIONS = {
        "random", "sorted", "reverse", "nearly_sorted", "few_distinct"
    };
    private static final String[] TYPES = {"Integer", "String"};
    private static final String[] ALGORITHMS = {
        "PassPerItem", "UntilNoChange", "WhileNeeded"
    };
    private static final long TARGET_NS = 5_000_000L;
    private static final int MAX_BATCH = 1024;
    private static final long WARMUP_NS = 4_000_000_000L;
    private static volatile long sink;
    private static final List<GarbageCollectorMXBean> GC =
        ManagementFactory.getGarbageCollectorMXBeans();

    private record Condition(int size, int distribution, int type, int algorithm) {
        int index() {
            return (((sizeIndex(size) * DISTRIBUTIONS.length + distribution)
                    * TYPES.length + type) * ALGORITHMS.length + algorithm);
        }
    }
    private record Measurement(long elapsedNs, long gcCount, long gcTimeMs) { }
    private record GcSnapshot(long count, long timeMs) { }

    private Benchmark() { }

    /** args: output-directory fork-number measured-blocks master-seed. */
    public static void main(String[] args) throws IOException {
        if (args.length != 4) {
            throw new IllegalArgumentException(
                "Usage: Benchmark output-directory fork-number measured-blocks master-seed");
        }
        Path out = Path.of(args[0]);
        int fork = Integer.parseInt(args[1]);
        int blocks = Integer.parseInt(args[2]);
        long master = Long.parseLong(args[3]);
        if (fork < 1 || blocks < 1) throw new IllegalArgumentException("Positive fork and blocks required");
        Files.createDirectories(out);
        String suffix = String.format(Locale.ROOT, "%02d", fork);
        List<Condition> conditions = conditions();
        long startWall = System.nanoTime();
        writeMetadata(out.resolve("metadata-fork-" + suffix + ".properties"), fork, blocks, master);

        // Complete rounds guarantee coverage; minimum wall time permits tiered JIT compilation.
        long warmStart = System.nanoTime();
        int warmRound = 0;
        do {
            for (Condition c : shuffled(conditions, seed(master, fork, -1000 - warmRound, 0, 0))) {
                measure(c, input(c, seed(master, fork, -1000 - warmRound,
                        c.size, c.distribution)), 1);
            }
            warmRound++;
        } while (System.nanoTime() - warmStart < WARMUP_NS);
        long warmActual = System.nanoTime() - warmStart;

        int[] batchCounts = new int[conditions.size()];
        try (BufferedWriter calibration = writer(out.resolve("calibration-fork-" + suffix + ".csv"))) {
            calibration.write("fork,type,size,distribution,algorithm,batch,pilot_ns\n");
            for (Condition c : shuffled(conditions, seed(master, fork, -10, 0, 0))) {
                Object base = input(c, seed(master, fork, -10, c.size, c.distribution));
                // A warmed single-sort pilot determines a bounded batch. Calibration is not data.
                long pilot = Math.max(1L, measure(c, base, 1).elapsedNs);
                int batch = (int) Math.max(1L, Math.min(MAX_BATCH,
                        (TARGET_NS + pilot - 1L) / pilot));
                batchCounts[c.index()] = batch;
                calibration.write(fork + "," + TYPES[c.type] + "," + c.size + ","
                        + DISTRIBUTIONS[c.distribution] + "," + ALGORITHMS[c.algorithm]
                        + "," + batch + "," + pilot + "\n");
            }
        }
        // Settle after calibration, including the exact measured batch sizes.
        for (int b = -2; b < 0; b++) {
            for (Condition c : shuffled(conditions, seed(master, fork, b, 0, 0))) {
                measure(c, input(c, seed(master, fork, b, c.size, c.distribution)),
                        batchCounts[c.index()]);
            }
        }
        try (BufferedWriter raw = writer(out.resolve("raw-fork-" + suffix + ".csv"))) {
            raw.write("fork,block,order,input_seed,type,size,distribution,algorithm,batch,elapsed_ns,ns_per_sort,gc_count,gc_time_ms\n");
            for (int block = 0; block < blocks; block++) {
                int order = 0;
                for (Condition c : shuffled(conditions, seed(master, fork, block, 0, 0))) {
                    long inputSeed = seed(master, fork, block, c.size, c.distribution);
                    int batch = batchCounts[c.index()];
                    Measurement result = measure(c, input(c, inputSeed), batch);
                    raw.write(String.format(Locale.ROOT,
                        "%d,%d,%d,%d,%s,%d,%s,%s,%d,%d,%.9f,%d,%d%n",
                        fork, block + 1, ++order, inputSeed, TYPES[c.type], c.size,
                        DISTRIBUTIONS[c.distribution], ALGORITHMS[c.algorithm], batch,
                        result.elapsedNs, result.elapsedNs / (double) batch,
                        result.gcCount, result.gcTimeMs));
                }
                raw.flush();
            }
        }
        try (BufferedWriter log = writer(out.resolve("run-fork-" + suffix + ".txt"))) {
            log.write("warmup_rounds=" + warmRound + "\nwarmup_ns=" + warmActual
                    + "\nsettling_blocks=2\nsink=" + sink + "\nwall_ns="
                    + (System.nanoTime() - startWall) + "\ncompleted_utc=" + Instant.now() + "\n");
        }
        System.out.println("Completed fork " + fork + " with " + blocks
                + " measured blocks after " + warmRound + " warmup rounds");
    }

    private static BufferedWriter writer(Path path) throws IOException {
        return Files.newBufferedWriter(path, StandardCharsets.UTF_8);
    }

    private static int sizeIndex(int size) {
        for (int i = 0; i < SIZES.length; i++) if (SIZES[i] == size) return i;
        throw new IllegalArgumentException("Unknown size");
    }

    private static List<Condition> conditions() {
        List<Condition> conditions = new ArrayList<>();
        for (int size : SIZES) for (int d = 0; d < DISTRIBUTIONS.length; d++)
            for (int t = 0; t < TYPES.length; t++) for (int a = 0; a < ALGORITHMS.length; a++)
                conditions.add(new Condition(size, d, t, a));
        return conditions;
    }

    private static List<Condition> shuffled(List<Condition> original, long seed) {
        List<Condition> result = new ArrayList<>(original);
        Collections.shuffle(result, new Random(seed));
        return result;
    }

    /** Input seeds omit data type and algorithm, preserving paired numeric ranks. */
    private static long seed(long master, int fork, int block, int size, int distribution) {
        long z = master ^ ((long) fork * 0x9e3779b97f4a7c15L)
                ^ ((long) block * 0xbf58476d1ce4e5b9L)
                ^ ((long) size * 0x94d049bb133111ebL) ^ distribution;
        z = (z ^ (z >>> 30)) * 0xbf58476d1ce4e5b9L;
        z = (z ^ (z >>> 27)) * 0x94d049bb133111ebL;
        return z ^ (z >>> 31);
    }

    private static Object input(Condition c, long seed) {
        int[] ranks = ranks(c.size, DISTRIBUTIONS[c.distribution], seed);
        if (c.type == 0) {
            Integer[] values = new Integer[c.size];
            for (int i = 0; i < values.length; i++) values[i] = ranks[i];
            return values;
        }
        String[] values = new String[c.size];
        for (int i = 0; i < values.length; i++)
            values[i] = String.format(Locale.ROOT, "%08d", ranks[i]);
        return values;
    }

    /** Public within the package so the generated input families can be tested. */
    static int[] ranks(int size, String distribution, long seed) {
        int[] keys = new int[size];
        SplittableRandom random = new SplittableRandom(seed);
        for (int i = 0; i < size; i++) keys[i] = i;
        switch (distribution) {
            case "random":
                for (int i = size - 1; i > 0; i--) {
                    int j = random.nextInt(i + 1), old = keys[i];
                    keys[i] = keys[j]; keys[j] = old;
                }
                break;
            case "sorted": break;
            case "reverse":
                for (int i = 0; i < size; i++) keys[i] = size - 1 - i;
                break;
            case "nearly_sorted":
                int[] starts = new int[size / 2];
                for (int i = 0; i < starts.length; i++) starts[i] = 2 * i;
                int swaps = Math.min(starts.length, Math.max(1, size / 100));
                for (int i = 0; i < swaps; i++) {
                    int j = i + random.nextInt(starts.length - i), old = starts[i];
                    starts[i] = starts[j]; starts[j] = old;
                    int index = starts[i], key = keys[index];
                    keys[index] = keys[index + 1]; keys[index + 1] = key;
                }
                break;
            case "few_distinct":
                for (int i = 0; i < size; i++) keys[i] = random.nextInt(16);
                break;
            default: throw new IllegalArgumentException("Unknown distribution " + distribution);
        }
        return keys;
    }

    @SuppressWarnings("unchecked")
    private static Measurement measure(Condition c, Object base, int batch) {
        // All cloning/allocation and input construction happen before the clock starts.
        if (c.type == 0) {
            Integer[] expected = ((Integer[]) base).clone();
            Arrays.sort(expected);
            Integer[][] arrays = new Integer[batch][];
            for (int i = 0; i < batch; i++) arrays[i] = ((Integer[]) base).clone();
            Sorter<Integer> sorter = sorter(c.algorithm);
            GcSnapshot before = gcSnapshot();
            long begin = System.nanoTime();
            for (Integer[] items : arrays) sorter.sort(items);
            long elapsed = System.nanoTime() - begin;
            GcSnapshot after = gcSnapshot();
            for (Integer[] items : arrays) consumeAndCheck(items, expected);
            return new Measurement(elapsed, after.count - before.count, after.timeMs - before.timeMs);
        }
        String[] expected = ((String[]) base).clone();
        Arrays.sort(expected);
        String[][] arrays = new String[batch][];
        for (int i = 0; i < batch; i++) arrays[i] = ((String[]) base).clone();
        Sorter<String> sorter = sorter(c.algorithm);
        GcSnapshot before = gcSnapshot();
        long begin = System.nanoTime();
        for (String[] items : arrays) sorter.sort(items);
        long elapsed = System.nanoTime() - begin;
        GcSnapshot after = gcSnapshot();
        for (String[] items : arrays) consumeAndCheck(items, expected);
        return new Measurement(elapsed, after.count - before.count, after.timeMs - before.timeMs);
    }

    static <T extends Comparable<T>> Sorter<T> sorter(int algorithm) {
        return switch (algorithm) {
            case 0 -> new BubbleSortPassPerItem<>();
            case 1 -> new BubbleSortUntilNoChange<>();
            case 2 -> new BubbleSortWhileNeeded<>();
            default -> throw new IllegalArgumentException("Unknown algorithm");
        };
    }

    private static <T extends Comparable<T>> void consumeAndCheck(T[] items, T[] expected) {
        if (!Arrays.equals(items, expected))
            throw new AssertionError("Output differs from Arrays.sort reference");
        long checksum = items.length;
        for (int i = 0; i < items.length; i++) {
            if (i > 0 && items[i - 1].compareTo(items[i]) > 0)
                throw new AssertionError("Incorrect output");
            checksum = checksum * 31L + items[i].hashCode();
        }
        sink ^= checksum;
    }

    private static GcSnapshot gcSnapshot() {
        long count = 0, time = 0;
        for (GarbageCollectorMXBean bean : GC) {
            if (bean.getCollectionCount() >= 0) count += bean.getCollectionCount();
            if (bean.getCollectionTime() >= 0) time += bean.getCollectionTime();
        }
        return new GcSnapshot(count, time);
    }

    private static void writeMetadata(Path path, int fork, int blocks, long master) throws IOException {
        Properties p = new Properties();
        for (String name : new String[]{"java.version", "java.runtime.version", "java.vendor",
                "java.vm.name", "java.vm.version", "os.name", "os.version", "os.arch"})
            p.setProperty(name, System.getProperty(name));
        p.setProperty("jvm.arguments", ManagementFactory.getRuntimeMXBean().getInputArguments().toString());
        p.setProperty("available.processors", "" + Runtime.getRuntime().availableProcessors());
        p.setProperty("max.heap.bytes", "" + Runtime.getRuntime().maxMemory());
        p.setProperty("fork", "" + fork);
        p.setProperty("measured.blocks", "" + blocks);
        p.setProperty("master.seed", "" + master);
        p.setProperty("warmup.minimum.ns", "" + WARMUP_NS);
        p.setProperty("batch.target.ns", "" + TARGET_NS);
        p.setProperty("batch.maximum", "" + MAX_BATCH);
        p.setProperty("start.utc", Instant.now().toString());
        p.setProperty("gc.names", GC.stream().map(GarbageCollectorMXBean::getName).toList().toString());
        try (BufferedWriter writer = writer(path)) {
            p.store(writer, "Bubble-sort experiment environment; contains no account identifiers");
        }
    }
}
