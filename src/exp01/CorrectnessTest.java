package exp01;

import java.util.Arrays;
import java.util.Locale;
import java.util.SplittableRandom;

/** Deterministic tests independent of the measured elapsed times. */
public final class CorrectnessTest {
    private static long checks;
    private record Tagged(int key, int originalPosition) implements Comparable<Tagged> {
        public int compareTo(Tagged other) { return Integer.compare(key, other.key); }
    }

    private CorrectnessTest() { }

    public static void main(String[] args) {
        // Exhaustive short arrays over a three-key alphabet include every small duplicate pattern.
        for (int n = 0; n <= 7; n++) {
            int combinations = 1;
            for (int j = 0; j < n; j++) combinations *= 3;
            for (int code = 0; code < combinations; code++) {
                Integer[] values = new Integer[n];
                int digits = code;
                for (int j = 0; j < n; j++) { values[j] = digits % 3 - 1; digits /= 3; }
                check(values);
                check(strings(values));
            }
        }
        check(new Integer[]{Integer.MAX_VALUE, 0, Integer.MIN_VALUE, -1, 1});
        check(new String[]{"", "a", "aa", "A", "00000001", "0", "a"});
        SplittableRandom random = new SplittableRandom(20261109L);
        for (int trial = 0; trial < 2000; trial++) {
            int n = random.nextInt(97);
            Integer[] values = new Integer[n];
            Tagged[] tagged = new Tagged[n];
            for (int j = 0; j < n; j++) {
                values[j] = random.nextInt(-100, 101);
                tagged[j] = new Tagged(random.nextInt(8), j);
            }
            check(values);
            check(strings(values));
            // Arrays.sort on object arrays is stable. Full equality also checks original positions.
            check(tagged);
        }
        for (int size : new int[]{128, 512, 2048}) {
            for (String family : new String[]{"random", "sorted", "reverse", "nearly_sorted", "few_distinct"}) {
                int[] keys = Benchmark.ranks(size, family, 42L);
                Integer[] values = Arrays.stream(keys).boxed().toArray(Integer[]::new);
                check(values); check(strings(values));
                if (!family.equals("few_distinct")) {
                    int[] sorted = keys.clone(); Arrays.sort(sorted);
                    for (int i = 0; i < size; i++) if (sorted[i] != i)
                        throw new AssertionError("Input is not a distinct permutation");
                } else {
                    for (int key : keys) if (key < 0 || key >= 16)
                        throw new AssertionError("Incorrect few-distinct key");
                }
                if (family.equals("nearly_sorted")) {
                    long inversions = 0;
                    for (int i = 0; i < size; i++) for (int j = i + 1; j < size; j++)
                        if (keys[i] > keys[j]) inversions++;
                    if (inversions != Math.max(1, size / 100))
                        throw new AssertionError("Incorrect nearly-sorted inversion count");
                }
            }
        }
        System.out.println("PASS: " + checks + " sorter versus Arrays.sort checks; input-family invariants verified");
    }

    private static String[] strings(Integer[] values) {
        String[] result = new String[values.length];
        for (int i = 0; i < values.length; i++)
            result[i] = String.format(Locale.ROOT, "%08d", values[i]);
        return result;
    }

    private static <T extends Comparable<T>> void check(T[] input) {
        T[] expected = input.clone(); Arrays.sort(expected);
        for (int algorithm = 0; algorithm < 3; algorithm++) {
            T[] actual = input.clone(); Benchmark.<T>sorter(algorithm).sort(actual);
            if (!Arrays.equals(actual, expected))
                throw new AssertionError("Algorithm " + algorithm + " failed on " + Arrays.toString(input));
            checks++;
        }
    }
}
